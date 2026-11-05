"""agentic loop：在一个用户回合内连续执行多步命令。

设计见 docs/DESIGN-agentic-loop.md。三条边界：

1. `#STEP:` 显式续步；普通命令仍然"执行完就结束本回合"，loop 关闭时行为与原来一致。
2. `risk_tier` 只决定要不要人工确认；安全拦截仍走 `is_dangerous` / `is_root_shell`。
3. 只从 wocc 路径导入，单轮 `woc` 的模块图与启动开销不受影响。
"""

import re
import shlex
import shutil
import sys
import textwrap
import time

from . import debug
from .colors import C, cout, eprint, interactive_input, read_key
from .config import fold_lines as fold_lines_config
from .config import spinner as spinner_enabled
from .config import syntax_check as syntax_check_enabled
from .exec import (format_exec_result, format_foreground_result, needs_privilege,
                   pty_capture_available, run_command_capture,
                   run_command_capture_pty, run_command_foreground,
                   sudo_ticket_valid, syntax_error)
from .llm import LLMError, StreamMode, call_llm_messages
from .prompts import EXPLAIN_SYSTEM_PROMPT
from .sanitize import (ChatResponse, CommandGenError, dangerous_reason,
                       has_toolcall, is_dangerous, is_root_shell, sanitize,
                       strip_dsml)
from .ui import Spinner

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

# 配置优先；这里只是防止误配的绝对上限（默认 5，README 允许用户调大）。
MAX_STEPS_LIMIT = 50

# 输出折叠：只折叠"成功的命令 stdout"，stderr / 失败 / 模型回答都不折。
FOLD_LINES = 25
FOLD_HEAD = 15
FOLD_TAIL = 8

# 上一条命令与完整输出，供 `:more` 重新展开。
_LAST = {"command": "", "output": "", "stderr": "", "code": None}

# 本回合执行过的步骤，供 `:steps` 审计。每个用户回合开始时清空。
_STEPS = []

# 中文正文出现在"命令"里，通常意味着模型漏了 #CHAT: 标记。
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


class SessionEnd(Exception):
    """用户在确认一步时按了 Ctrl+C / EOF，调用方应结束整个会话。"""


def is_pure_simple_cmd(cmd):
    """是否是不含管道 / 替换 / 重定向的单条命令。"""
    if not cmd or any(char in cmd for char in _UNBOUNDED):
        return False
    return True


# 会"把文件内容打出来"的只读命令：路径参数要单独复核（见 read_only_reason）。
_CONTENT_READERS = frozenset({"cat", "head", "tail", "grep"})

# 这些路径里装的是**环境变量**（token / key），不是普通文件内容。
_ENV_PATH_RE = re.compile(r"(?:^|/)proc(?:/|$)|(?:^|/)sys(?:/|$)|environ", re.I)

# `ps` 的 BSD `e` 旗标会把进程环境变量一起打印（`ps auxe`）。`-e` / `-ef` 只是
# "选所有进程"，不带环境，所以只对"字母组合里同时有 e 和 a/u/x"的写法降级。
_PS_BSD_ENV_NEIGHBOURS = "aux"


def read_only_reason(cmd):
    """`READ_ONLY` 白名单的参数级复核：返回降级原因，安全则返回 None。

    `cat` / `ps` 是只读的，但会把**别的进程的环境变量**摊在屏幕上：
    `cat /proc/self/environ`、`ps auxe` 在 `auto_safe=true` 下会免确认执行，
    输出再回灌给模型、再落进会话文件。这里把它们降级到 `confirm`
    （不 deny：`ps auxe` 有正当用途，`/proc` 也可能只是查内核参数）。
    """
    try:
        argv = shlex.split(cmd)
    except ValueError:
        argv = cmd.split()
    if not argv:
        return None
    head, args = argv[0], argv[1:]

    if head == "ps":
        for arg in args:
            if arg.startswith("-"):
                letters = arg.lstrip("-")
                if "e" in letters and any(ch in letters for ch in _PS_BSD_ENV_NEIGHBOURS):
                    return "ps 的 e 旗标会打印进程环境变量"
                continue
            if "e" in arg:
                return "ps 的 e 旗标会打印进程环境变量"
        return None

    if head in _CONTENT_READERS:
        for arg in args:
            if arg.startswith("-"):
                continue
            if _ENV_PATH_RE.search(arg):
                return "读取 /proc、/sys 或 environ（进程环境变量）"
    return None


def risk_tier(cmd):
    """safe / confirm / deny —— 只决定要不要人工确认，不决定要不要执行。"""
    if is_dangerous(cmd) or is_root_shell(cmd):
        return "deny"
    if not is_pure_simple_cmd(cmd):
        return "confirm"
    if cmd.split()[0] not in READ_ONLY:
        return "confirm"
    return "confirm" if read_only_reason(cmd) else "safe"


def max_steps_for(cfg, loop_enabled=True):
    """生效步数：配置优先，MAX_STEPS_LIMIT 只是防误配的绝对上限。"""
    settings = cfg.get("loop") or {}
    try:
        value = int(settings.get("max_steps", 5))
    except (TypeError, ValueError):
        value = 5
    value = min(max(value, 1), MAX_STEPS_LIMIT)
    return value if loop_enabled else 1


def _limits(cfg, loop_enabled):
    settings = cfg.get("loop") or {}
    try:
        total_chars = int(settings.get("total_chars", 24000))
    except (TypeError, ValueError):
        total_chars = 24000
    return (max_steps_for(cfg, loop_enabled), max(total_chars, 1),
            bool(settings.get("auto_safe")))


def _show_command(alias, cmd):
    """命令短就跳过（上一行已经流式打印过），长命令在确认前整条重排一遍。

    终端按列硬折行，会把管道两头的命令拆成两半；这里按词折行并缩进，
    保证"要执行的到底是什么"在确认这一屏上还能读全。
    """
    columns = shutil.get_terminal_size((80, 24)).columns
    width = max(40, columns - 4)
    if len(cmd) <= width:
        return
    eprint(f"[{alias}] 完整命令：", C.GRAY)
    for line in textwrap.wrap(cmd, width=width,
                              subsequent_indent="    ",
                              break_long_words=True,
                              break_on_hyphens=False):
        eprint("  " + line, C.CYAN)


def _confirm(alias):
    """读一个确认键。Enter/y 执行，n 跳过，e 编辑。

    只认这几个键：方向键和功能键的首字节是 ESC，不能被当成“执行”。
    """
    eprint(f"[{alias}] Enter/y 执行 · n 跳过 · e 编辑 · Ctrl+C 取消", C.GREEN)
    hinted = False
    while True:
        try:
            key = read_key()
        except (KeyboardInterrupt, EOFError):
            eprint(f"\n[{alias}] 会话结束", C.GREEN)
            raise SessionEnd()
        key = (key or "").strip().lower()
        if key in ("", "y", "yes"):
            return "run"
        if key in ("n", "s", "no", "skip"):
            return "skip"
        if key in ("e", "edit"):
            return "edit"
        if not hinted:
            eprint(f"[{alias}] 只认 Enter/y · n · e，其它键已忽略",
                   C.YELLOW)
            hinted = True


def _print_output(out, err, code, alias, limit=FOLD_LINES):
    """打印命令输出。stdout 过长才折叠；stderr 与失败时永远完整显示。"""
    folded = False
    if out:
        lines = out.splitlines()
        if limit > 0 and code == 0 and len(lines) > limit:
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


def export_last():
    """导出上一条命令与输出，供会话落盘。"""
    return dict(_LAST)


def restore_last(data):
    """恢复上一条命令与输出（`--last-session`），让 :last/:more/:explain 继续可用。"""
    if not isinstance(data, dict):
        return
    for key in ("command", "output", "stderr"):
        value = data.get(key)
        if isinstance(value, str):
            _LAST[key] = value
    code = data.get("code")
    if code is None or isinstance(code, int):
        _LAST["code"] = code


def show_steps(alias):
    """打印本回合的步骤审计（`:steps`）。返回是否有内容。"""
    if not _STEPS:
        eprint(f"[{alias}] 本回合还没有执行过命令", C.YELLOW)
        return False
    eprint(f"[{alias}] 本回合 {len(_STEPS)} 步：", C.GREEN, C.BOLD)
    for item in _STEPS:
        status = item["status"]
        if status == "run":
            label = "%s  %.2fs" % (item.get("code", "?"),
                                    item.get("ms", 0) / 1000.0)
        else:
            label = {"skip": "跳过", "deny": "拒绝",
                     "syntax": "语法错误"}.get(status, status)
        eprint(f"  {item['step']}. [{label}] {item['cmd'][:140]}", C.GRAY)
    return True


EXPLAIN_OUTPUT_CHARS = 8000


def explain_last(alias, cfg):
    """解释上一条命令与输出（`:explain`）。只读请求，不改变会话历史。"""
    command = _LAST["command"]
    if not command:
        eprint(f"[{alias}] 还没有执行过命令", C.YELLOW)
        return False
    stdout = _LAST["output"][:EXPLAIN_OUTPUT_CHARS] or "(无 stdout)"
    stderr = _LAST["stderr"][:EXPLAIN_OUTPUT_CHARS] or "(无 stderr)"
    block = (
        "<command>\n%s\n</command>\n"
        "<exit_code>%s</exit_code>\n"
        "<stdout>\n%s\n</stdout>\n"
        "<stderr>\n%s\n</stderr>\n\n"
        "请解释这条命令做了什么，以及输出说明了什么。"
        % (command, _LAST["code"], stdout, stderr)
    )
    messages = [
        {"role": "system",
         "content": EXPLAIN_SYSTEM_PROMPT.replace("{name}", alias)},
        {"role": "user", "content": block},
    ]
    state = {"started": False, "tail": ""}
    spin = Spinner(f"[{alias}] Thinking", enabled=spinner_enabled(cfg))

    def on_chunk(text):
        if not state["started"]:
            state["started"] = True
            spin.stop()
            sys.stdout.write("\n")
        state["tail"] = text[-1:]
        sys.stdout.write(text)
        sys.stdout.flush()

    spin.start()
    try:
        call_llm_messages(messages, cfg, on_chunk=on_chunk)
    except LLMError as exc:
        eprint(f"[{alias}] {exc}", C.RED)
        return False
    finally:
        spin.stop()
    if not state["started"] or state["tail"] != "\n":
        sys.stdout.write("\n")
    sys.stdout.flush()
    return True


def rerun_last(messages, alias, cfg, capture=False):
    """重跑上一条命令（`:again`）：仍然过 gate + 人工确认，并写回会话历史。"""
    cmd = _LAST["command"]
    if not cmd:
        eprint(f"[{alias}] 还没有执行过命令", C.YELLOW)
        return False
    if is_root_shell(cmd) or is_dangerous(cmd):
        eprint(f"[{alias}] 该命令现在会被安全策略拒绝，未执行：", C.YELLOW, C.BOLD)
        eprint(f"  {cmd}", C.YELLOW)
        return False
    if syntax_check_enabled(cfg):
        error = syntax_error(cmd)
        if error:
            eprint(f"[{alias}] 命令语法错误，未执行：", C.YELLOW, C.BOLD)
            eprint(f"  {cmd[:200]}", C.YELLOW)
            return False

    eprint(f"[{alias}] 将重跑：", C.GREEN)
    eprint(f"  {cmd}", C.CYAN)
    action = _confirm(alias)
    if action == "skip":
        eprint(f"[{alias}] 已跳过", C.YELLOW)
        return False
    if action == "edit":
        try:
            edited = interactive_input(
                prompt="", initial=cmd,
                notice=lambda msg: eprint(f"[{alias}] {msg}",
                                           C.YELLOW),
            ).strip()
        except (KeyboardInterrupt, EOFError):
            eprint(f"\n[{alias}] 已取消", C.YELLOW)
            return False
        if not edited:
            eprint(f"[{alias}] 已跳过", C.YELLOW)
            return False
        if is_root_shell(edited) or is_dangerous(edited):
            eprint(f"[{alias}] 编辑后的命令仍被拒绝，未执行", C.YELLOW, C.BOLD)
            return False
        cmd = edited

    result, out, err, code, shown = _execute(
        cmd, alias, capture, spinner=spinner_enabled(cfg),
        label=f"[{alias}] Running")
    _LAST.update(command=cmd, output=out, stderr=err, code=code)
    if not shown:
        _print_output(out, err, code, alias, limit=fold_lines_config(cfg))
    if code != 0:
        eprint(f"[{alias}] exit_code={code}", C.RED)
    messages.append({"role": "assistant", "content": cmd})
    messages.append({"role": "user", "content": result})
    return True


def _ask(messages, cfg, alias, spinner=False, step=None, max_steps=None):
    """流式请求一轮，返回模型的原始文本（含 #STEP: 标记）。"""
    state = {"mode": None, "started": False}
    label = f"[{alias}] Thinking"
    if step is not None and max_steps and max_steps > 1:
        label = f"[{alias}] Step {step}/{max_steps} - Thinking"
    spin = Spinner(label, enabled=spinner)

    def on_mode(mode):
        state["mode"] = mode

    def on_chunk(text):
        if not state["started"]:
            state["started"] = True
            spin.stop()
            sys.stdout.write("\n" + cout("[assistant] ", C.GREEN, C.BOLD))
        if state["mode"] == StreamMode.COMMAND:
            sys.stdout.write(cout(text, C.CYAN, C.BOLD))
        else:
            sys.stdout.write(text)
        sys.stdout.flush()

    spin.start()
    try:
        raw = call_llm_messages(messages, cfg, on_chunk=on_chunk, on_mode=on_mode)
    except LLMError as exc:
        spin.stop()
        sys.stdout.write("\n")
        sys.stdout.flush()
        eprint(f"[{alias}] LLM 调用失败：{exc}", C.RED)
        eprint(f"[{alias}] 本轮已跳过，可换个说法重试，或 :q 退出", C.YELLOW)
        raise
    finally:
        spin.stop()
    sys.stdout.write("\n")
    sys.stdout.flush()
    return raw


def _execute(cmd, alias, capture, spinner=False, label=""):
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

    spin = Spinner(label or f"[{alias}] Running", enabled=spinner)
    spin.start()
    try:
        out, err, code = run_command_capture(cmd)
    finally:
        spin.stop()
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

    loop 开启后采用"粘性续步"：本回合一旦出现过 #STEP:，后面的裸命令也算一步，
    只有 #CHAT: / #ERROR: 结束回合；从未用过 #STEP: 的单命令回合仍是一次请求结束。
    """
    _STEPS.clear()
    max_steps, total_chars, auto_safe = _limits(cfg, loop_enabled)
    fold_limit = fold_lines_config(cfg)
    show_spinner = spinner_enabled(cfg)
    check_syntax = syntax_check_enabled(cfg)
    spent = 0
    last_cmd = None
    multi_step = False

    for step in range(1, max_steps + 1):
        raw = _ask(
            messages, cfg, alias, spinner=show_spinner,
            step=step if multi_step else None,
            max_steps=max_steps if multi_step else None,
        )
        text = strip_dsml(raw).strip()
        step_requested = text.lstrip().startswith(STEP_MARKER)
        body = (text.lstrip()[len(STEP_MARKER):].strip()
                if step_requested else text)
        step_mode = step_requested and loop_enabled
        if step_mode:
            multi_step = True

        try:
            # run_turn 会先剥 DSML，所以守卫必须看 raw，而不是剥完的 body。
            if has_toolcall(raw) and not text.lstrip().startswith("#CHAT:"):
                raise CommandGenError(
                    "模型输出了工具调用/标签格式，已拒绝执行；"
                    "请只返回一条命令，或 #CHAT: 回答")
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
            if step_mode or multi_step:
                continue
            return

        # 和 #CHAT: 同理：历史里保留 #STEP:，否则模型会慢慢退回"裸命令"格式。
        assistant_content = (STEP_MARKER + " " + cmd) if step_mode else cmd
        messages.append({"role": "assistant", "content": assistant_content})
        record = {"step": step, "cmd": cmd, "status": "run"}

        if is_root_shell(cmd):
            block = _rejected(
                alias, cmd,
                "该命令会打开交互式 root shell，已拒绝：",
                f"命令会打开交互式 root shell，{alias} 无法在其中继续工作；"
                "请改成具体的非交互命令。",
            )
            record["status"] = "deny"
            _STEPS.append(record)
            messages.append({"role": "user", "content": block})
            if step_mode or multi_step:
                continue
            return

        tier = risk_tier(cmd)
        if debug.enabled():
            debug.log("step %d/%d: tier=%s cmd=%r" % (step, max_steps, tier, cmd[:80]))
        if tier == "deny":
            record["status"] = "deny"
            _STEPS.append(record)
            reason = dangerous_reason(cmd) or "命中安全策略"
            block = _rejected(
                alias, cmd,
                f"检测到潜在危险命令（{reason}），已拒绝：",
                f"命令因安全策略被拒绝：{reason}。请换一种安全写法。",
            )
            messages.append({"role": "user", "content": block})
            if step_mode or multi_step:
                continue
            return

        if (step_mode or multi_step) and cmd == last_cmd:
            eprint(f"[{alias}] 模型连续两次给出同一条命令，已中止", C.YELLOW, C.BOLD)
            return
        last_cmd = cmd

        if check_syntax:
            error = syntax_error(cmd)
            if error:
                detail = "\n".join(error.strip().splitlines()[-2:])[:500]
                record["status"] = "syntax"
                _STEPS.append(record)
                eprint(f"[{alias}] 命令语法错误，未执行：", C.YELLOW, C.BOLD)
                eprint(f"  {cmd[:200]}", C.YELLOW)
                messages.append({
                    "role": "user",
                    "content": (
                        "<exec_result>\n"
                        "exit_code: 2\n"
                        "stdout:\n\n"
                        "stderr:\n"
                        f"命令语法错误（bash -n）：{detail}\n"
                        "请修正为一条合法的单行命令。\n"
                        "</exec_result>"
                    ),
                })
                if step_mode or multi_step:
                    continue
                return

        if not (tier == "safe" and auto_safe and loop_enabled):
            _show_command(alias, cmd)
            action = _confirm(alias)
            if action == "edit":
                if not sys.stdin.isatty():
                    eprint(f"[{alias}] 非交互模式下无法编辑，已跳过", C.YELLOW)
                    action = "skip"
                else:
                    edited = interactive_input(
                        prompt="", initial=cmd,
                        notice=lambda msg: eprint(f"[{alias}] {msg}",
                                                   C.YELLOW),
                    ).strip()
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
                            messages[-1]["content"] = (
                                STEP_MARKER + " " + cmd) if step_mode else cmd
                            action = "run"
            if action == "skip":
                record["status"] = "skip"
                _STEPS.append(record)
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

        run_label = (f"[{alias}] Step {step}/{max_steps} - Running"
                     if (step_mode or multi_step) and max_steps > 1
                     else f"[{alias}] Running")
        started = time.monotonic()
        result, out, err, code, shown = _execute(
            cmd, alias, capture, spinner=show_spinner, label=run_label)
        record["code"] = code
        record["ms"] = int((time.monotonic() - started) * 1000)
        _STEPS.append(record)
        _LAST["command"] = cmd
        _LAST["output"] = out
        _LAST["stderr"] = err
        _LAST["code"] = code
        if not shown:
            _print_output(out, err, code, alias, limit=fold_limit)
        if code != 0:
            eprint(f"[{alias}] exit_code={code}", C.RED)
        if code == 127 and _CJK_RE.search(cmd):
            # 可观测性：模型偶尔会忘记 #CHAT:，把一段中文回答当成命令。这里不改行为，
            # 只在终端提示一句，方便下次回归时立刻发现（历史格式见 run_turn 的注释）。
            eprint(f"[{alias}] ⚠️  这条“命令”更像是一段回答（exit 127 且含中文），"
                   "可能是模型漏了 #CHAT: 标记。", C.YELLOW)

        messages.append({"role": "user", "content": result})
        spent += len(result)

        if not step_mode and not multi_step:
            return
        if spent > total_chars:
            eprint(f"[{alias}] 累计输出预算用尽（{total_chars} 字符），停止自动执行",
                   C.YELLOW, C.BOLD)
            return

    if loop_enabled:
        eprint(f"[{alias}] 已达到 {max_steps} 步上限，停止自动执行", C.YELLOW, C.BOLD)
