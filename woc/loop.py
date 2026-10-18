"""agentic loop：在一个用户回合内连续执行多步命令。

设计见 docs/DESIGN-agentic-loop.md。三条边界：

1. `#STEP:` 显式续步；普通命令仍然"执行完就结束本回合"，loop 关闭时行为与原来一致。
2. `risk_tier` 只决定要不要人工确认；安全拦截仍走 `is_dangerous` / `is_root_shell`。
3. 只从 wocc 路径导入，单轮 `woc` 的模块图与启动开销不受影响。
"""

import re
import sys

from .colors import C, cout, eprint, interactive_input, read_key
from .exec import (format_exec_result, format_foreground_result, needs_privilege,
                   pty_capture_available, run_command_capture,
                   run_command_capture_pty, run_command_foreground,
                   sudo_ticket_valid)
from .llm import LLMError, StreamMode, call_llm_messages
from .sanitize import (ChatResponse, CommandGenError, dangerous_reason,
                       is_dangerous, is_root_shell, sanitize, strip_dsml)

STEP_MARKER = "#STEP:"

# 只读白名单：只放"不会写盘、也不会执行别的程序"的命令。
# 刻意排除 find（-exec/-delete）、sort（-o 会写文件）、awk/sed（能执行命令）、
# tee、xargs、env、watch、timeout、sudo 等 —— 少几个白名单，换 0 个 safe 误判。
READ_ONLY = frozenset({
    "ls", "cat", "head", "tail", "wc", "stat", "file", "df", "du", "free",
    "ps", "uname", "id", "whoami", "date", "uptime", "which", "type",
    "readlink", "realpath", "pwd", "echo", "grep", "cut", "tr", "uniq",
    "basename", "dirname", "md5sum", "sha256sum", "lsblk", "tree",
})

# 出现任一字符就认为"行为无界"：管道、串联、替换、重定向、子 shell。
# 宁可多判一条 confirm，也不要让 `cat a | xargs rm` 落进 safe。
_UNBOUNDED = "|&;$`><()"

MAX_STEPS_LIMIT = 8

# 输出折叠：只折叠"成功的命令 stdout"，stderr / 失败 / 模型回答都不折。
FOLD_LINES = 25
FOLD_HEAD = 15
FOLD_TAIL = 8

# 上一条命令与完整输出，供 `:more` 重新展开。
_LAST = {"command": "", "output": ""}

# 中文正文出现在"命令"里，通常意味着模型漏了 #CHAT: 标记。
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


class SessionEnd(Exception):
    """用户在确认一步时按了 Ctrl+C / EOF，调用方应结束整个会话。"""


def is_pure_simple_cmd(cmd):
    """是否是不含管道 / 替换 / 重定向的单条命令。"""
    if not cmd or any(char in cmd for char in _UNBOUNDED):
        return False
    return True


def risk_tier(cmd):
    """safe / confirm / deny —— 只决定要不要人工确认，不决定要不要执行。"""
    if is_dangerous(cmd) or is_root_shell(cmd):
        return "deny"
    if not is_pure_simple_cmd(cmd):
        return "confirm"
    return "safe" if cmd.split()[0] in READ_ONLY else "confirm"


def _limits(cfg, loop_enabled):
    settings = cfg.get("loop") or {}
    max_steps = int(settings.get("max_steps", 5))
    total_chars = int(settings.get("total_chars", 24000))
    max_steps = min(max(max_steps, 1), MAX_STEPS_LIMIT)
    if not loop_enabled:
        max_steps = 1
    return max_steps, max(total_chars, 1), bool(settings.get("auto_safe"))


def _confirm(alias):
    """读一个确认键。Enter/y 执行，n 跳过，e 编辑。"""
    eprint(f"[{alias}] Enter/y 执行 · n 跳过 · e 编辑 · Ctrl+C 取消", C.GREEN)
    try:
        key = read_key()
    except (KeyboardInterrupt, EOFError):
        eprint(f"\n[{alias}] 会话结束", C.GREEN)
        raise SessionEnd()
    key = (key or "").strip().lower()
    if key in ("n", "s", "no", "skip"):
        return "skip"
    if key in ("e", "edit"):
        return "edit"
    return "run"


def _print_output(out, err, code, alias):
    """打印命令输出。stdout 过长才折叠；stderr 与失败时永远完整显示。"""
    folded = False
    if out:
        lines = out.splitlines()
        if code == 0 and len(lines) > FOLD_LINES:
            hidden = len(lines) - FOLD_HEAD - FOLD_TAIL
            sys.stdout.write("\n".join(lines[:FOLD_HEAD]) + "\n")
            sys.stdout.write(f"… 已折叠 {hidden} 行（输入 :more 展开全文）\n")
            sys.stdout.write("\n".join(lines[-FOLD_TAIL:]) + "\n")
            folded = True
        else:
            sys.stdout.write(out if out.endswith("\n") else out + "\n")
    if err:
        sys.stderr.write(err if err.endswith("\n") else err + "\n")
    sys.stdout.flush()
    sys.stderr.flush()
    return folded


def expand_last_output():
    """重新打印上一条命令的完整 stdout（`:more`）。"""
    if not _LAST["output"]:
        return False
    sys.stdout.write(_LAST["output"])
    if not _LAST["output"].endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()
    return True


def last_command():
    return _LAST["command"]


def _ask(messages, cfg, alias):
    """流式请求一轮，返回模型的原始文本（含 #STEP: 标记）。"""
    state = {"mode": None}

    def on_mode(mode):
        state["mode"] = mode

    def on_chunk(text):
        if state["mode"] == StreamMode.COMMAND:
            sys.stdout.write(cout(text, C.CYAN, C.BOLD))
        else:
            sys.stdout.write(text)
        sys.stdout.flush()

    sys.stdout.write("\n" + cout("[assistant] ", C.GREEN, C.BOLD))
    sys.stdout.flush()
    try:
        raw = call_llm_messages(messages, cfg, on_chunk=on_chunk, on_mode=on_mode)
    except LLMError as exc:
        sys.stdout.write("\n")
        sys.stdout.flush()
        eprint(f"[{alias}] LLM 调用失败：{exc}", C.RED)
        eprint(f"[{alias}] 本轮已跳过，可换个说法重试，或 :q 退出", C.YELLOW)
        raise
    sys.stdout.write("\n")
    sys.stdout.flush()
    return raw


def _execute(cmd, alias, capture):
    """执行一步，返回 (exec_result 文本, stdout, stderr, exit_code, 是否已回显)。"""
    if needs_privilege(cmd) and not sudo_ticket_valid():
        if not sys.stdin.isatty():
            eprint(f"[{alias}] 需要 sudo 密码，但当前输入不是终端，已拒绝：",
                   C.YELLOW, C.BOLD)
            eprint(f"  {cmd}", C.YELLOW)
            block = (
                "<exec_result>\n"
                "exit_code: 126\n"
                "stdout:\n\n"
                f"stderr:\n需要 sudo 密码，但 {alias} 的输入不是终端。"
                "请在终端里直接运行，或先 sudo -v 缓存凭证后重试。\n"
                "</exec_result>"
            )
            return block, "", "", 126, False

        if capture and pty_capture_available():
            eprint(f"[{alias}] 该命令需要 sudo 权限，正在 pty 中执行并捕获输出"
                   "（输出会反馈给模型）", C.YELLOW, C.BOLD)
            out, err, code = run_command_capture_pty(cmd)
            return format_exec_result(out, err, code), out, err, code, True

        if capture:
            eprint(f"[{alias}] 找不到 script，无法捕获输出，改为前台执行",
                   C.YELLOW, C.BOLD)
        eprint(f"[{alias}] 该命令需要 sudo 权限，将在本终端前台执行"
               "（输出不会反馈给模型）", C.YELLOW, C.BOLD)
        code, ferr = run_command_foreground(cmd)
        if ferr:
            eprint(f"[{alias}] {ferr}", C.RED)
        return format_foreground_result(code, ferr), "", "", code, True

    out, err, code = run_command_capture(cmd)
    return format_exec_result(out, err, code), out, err, code, False


def _rejected(alias, cmd, reason, hint):
    """安全拦截的统一回灌块（与危险命令路径一致：exit_code 126）。"""
    eprint(f"[{alias}] {reason}", C.YELLOW, C.BOLD)
    eprint(f"  {cmd}", C.YELLOW)
    return (
        "<exec_result>\n"
        "exit_code: 126\n"
        "stdout:\n\n"
        f"stderr:\n{hint}\n"
        "</exec_result>"
    )


def run_turn(messages, cfg, alias, capture=False, loop_enabled=False):
    """跑完一个用户回合；messages 以用户消息结尾，返回时已补全执行结果。

    `loop_enabled=False` 时行为与旧版 wocc 完全一致（只发一次请求、只执行一条命令）。
    """
    max_steps, total_chars, auto_safe = _limits(cfg, loop_enabled)
    spent = 0
    last_cmd = None

    for step in range(1, max_steps + 1):
        raw = _ask(messages, cfg, alias)
        text = strip_dsml(raw).strip()
        step_requested = text.lstrip().startswith(STEP_MARKER)
        body = (text.lstrip()[len(STEP_MARKER):].strip()
                if step_requested else text)
        step_mode = step_requested and loop_enabled

        try:
            cmd = sanitize(body)
        except ChatResponse as chat:
            # 历史里**保留** `#CHAT:` 标记：模型会模仿自己上一轮的回复格式，剥掉标记
            # 会让它下一轮继续只写正文（实测 6 轮会话 11/18 vs 保留标记 18/18）。
            messages.append({"role": "assistant", "content": "#CHAT: " + chat.text})
            return
        except CommandGenError as exc:
            eprint(f"[{alias}] {exc}", C.RED)
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": (
                    "<exec_result>\n"
                    "exit_code: 1\n"
                    "stdout:\n\n"
                    f"stderr:\n命令生成失败：{exc}\n"
                    "</exec_result>"
                ),
            })
            if step_mode:
                continue
            return

        messages.append({"role": "assistant", "content": cmd})

        if is_root_shell(cmd):
            block = _rejected(
                alias, cmd,
                "该命令会打开交互式 root shell，已拒绝：",
                f"命令会打开交互式 root shell，{alias} 无法在其中继续工作；"
                "请改成具体的非交互命令。",
            )
            messages.append({"role": "user", "content": block})
            if step_mode:
                continue
            return

        tier = risk_tier(cmd)
        if tier == "deny":
            reason = dangerous_reason(cmd) or "命中安全策略"
            block = _rejected(
                alias, cmd,
                f"检测到潜在危险命令（{reason}），已拒绝：",
                f"命令因安全策略被拒绝：{reason}。请换一种安全写法。",
            )
            messages.append({"role": "user", "content": block})
            if step_mode:
                continue
            return

        if step_mode and cmd == last_cmd:
            eprint(f"[{alias}] 模型连续两次给出同一条命令，已中止", C.YELLOW, C.BOLD)
            return
        last_cmd = cmd

        if loop_enabled and max_steps > 1:
            eprint(f"[{alias}] [step {step}/{max_steps}]", C.CYAN)

        if not (tier == "safe" and auto_safe and loop_enabled):
            action = _confirm(alias)
            if action == "edit":
                if not sys.stdin.isatty():
                    eprint(f"[{alias}] 非交互模式下无法编辑，已跳过", C.YELLOW)
                    action = "skip"
                else:
                    edited = interactive_input(prompt="", initial=cmd).strip()
                    if not edited:
                        action = "skip"
                    elif edited == cmd:
                        action = "run"
                    else:
                        reason = dangerous_reason(edited)
                        if reason or is_root_shell(edited):
                            eprint(f"[{alias}] 编辑后的命令仍被拒绝"
                                   f"（{reason or '交互式 root shell'}）",
                                   C.YELLOW, C.BOLD)
                            action = "skip"
                        else:
                            cmd = edited
                            messages[-1]["content"] = cmd
                            action = "run"
            if action == "skip":
                eprint(f"[{alias}] 已跳过", C.YELLOW)
                messages.append({
                    "role": "user",
                    "content": (
                        "<exec_result>\n"
                        "exit_code: 130\n"
                        "stdout:\n\n"
                        "stderr:\n用户跳过了这条命令。\n"
                        "</exec_result>"
                    ),
                })
                return

        result, out, err, code, shown = _execute(cmd, alias, capture)
        _LAST["command"] = cmd
        _LAST["output"] = out
        if not shown:
            _print_output(out, err, code, alias)
        if code != 0:
            eprint(f"[{alias}] exit_code={code}", C.RED)
        if code == 127 and _CJK_RE.search(cmd):
            # 可观测性：模型偶尔会忘记 #CHAT:，把一段中文回答当成命令。这里不改行为，
            # 只在终端提示一句，方便下次回归时立刻发现（历史格式见 run_turn 的注释）。
            eprint(f"[{alias}] ⚠️  这条“命令”更像是一段回答（exit 127 且含中文），"
                   "可能是模型漏了 #CHAT: 标记。", C.YELLOW)

        messages.append({"role": "user", "content": result})
        spent += len(result)

        if not step_mode:
            return
        if spent > total_chars:
            eprint(f"[{alias}] 累计输出预算用尽（{total_chars} 字符），停止自动执行",
                   C.YELLOW, C.BOLD)
            return

    if loop_enabled:
        eprint(f"[{alias}] 已达到 {max_steps} 步上限，停止自动执行", C.YELLOW, C.BOLD)
