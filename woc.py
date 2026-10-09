#!/usr/bin/env python3
"""
woc  - 用自然语言生成 bash 命令并执行（单轮）
wocc - 交互式多轮会话，自动把上一条命令的运行结果反馈给 LLM
"""

import os
import re
import sys
import readline  # noqa: F401
import json
import subprocess
import urllib.request
import urllib.error


# Outputs Color
def _supports_color(stream):
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("WOC_FORCE_COLOR"):
        return True
    try:
        return stream.isatty()
    except Exception:
        return False


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"

    def __init__(self, stream):
        self._on = _supports_color(stream)

    def __call__(self, text, *styles):
        if not self._on or not styles:
            return text
        return "".join(styles) + text + self.RESET


cerr = C(sys.stderr)
cout = C(sys.stdout)


def eprint(text="", *styles):
    """打印到 stderr，自动换行 + flush。"""
    sys.stderr.write(cerr(text, *styles) + "\n")
    sys.stderr.flush()


# Load Config
def load_config():
    cfg = {
        "api_base": os.environ.get("WOC_API_BASE", "https://api.deepseek.com"),
        "api_key": os.environ.get("WOC_API_KEY", ""),
        "model": os.environ.get("WOC_MODEL", "deepseek-flash"),
        "timeout": int(os.environ.get("WOC_TIMEOUT", "30")),
    }

    path = os.path.expanduser("~/.config/woc/config.toml")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if not v:
                    continue
                if k == "timeout":
                    try:
                        cfg[k] = int(v)
                    except ValueError:
                        print(f"[woc] 配置项 timeout 非法: {v!r}，已忽略", file=sys.stderr)
                elif k in cfg:
                    cfg[k] = v
    return cfg


# ---------- Prompt ----------
SYSTEM_PROMPT = """You are woc, a helpful Linux assistant living inside the user's terminal.

You operate in TWO modes. Pick the right one based on the user's intent:

[A] COMMAND MODE (default)
If the user is asking you to DO something on the system (look up, list, count, capture,
monitor, generate a file, run a tool...), output ONLY ONE single-line bash command.

Rules for command mode (STRICT):
1. Output ONLY ONE single-line bash command. No explanation, no markdown, no code fences, no backticks.
2. Do NOT prefix with "$", ">", "bash -c", or any shell prompt.
3. The command must be executable as-is in bash on Linux.
4. Prefer common tools (grep, awk, sed, find, top, ps, df, du, tar, curl, journalctl, systemctl...).
5. If the user's request is ambiguous, choose the most common/safe interpretation.
6. Never output multiple commands joined by ';' or '&&' unless strictly necessary.
7. If the request is impossible or unsafe, output exactly: #ERROR: <short reason>
8. Never wrap the command in quotes.

[B] CHAT MODE
If the user is clearly ASKING A QUESTION or wants an explanation/discussion rather than
wanting a command executed — e.g. "XX 是什么意思", "为什么...", "解释一下", "区别是什么",
"教我怎么...", "什么是...", or explicitly says "chat"/"聊天"/"不要执行" —
respond in plain natural language.

Rules for chat mode (STRICT):
1. Start your reply with the exact marker: #CHAT:
2. After the marker, answer concisely and helpfully in the user's language.
3. You MAY use multiple lines after the marker. Do NOT output any shell command.
4. Do NOT wrap the answer in code fences.

When in doubt, prefer command mode.

Examples:
User: 帮我抓一次实时资源的 snapshot 到 a.txt
Assistant: top -b -n 1 > a.txt

User: 查看当前目录下最大的 10 个文件
Assistant: du -ah . | sort -rh | head -n 10

User: 统计 nginx 日志里访问量前 10 的 IP
Assistant: awk '{print $1}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head -n 10

User: du -sh 和 du -h 有什么区别？
Assistant: #CHAT: -h 按人类可读单位显示（K/M/G），-s 只输出每个参数的总计，不列出子目录明细。二者常一起用：du -sh . 表示"当前目录总共占多大"。

User: 解释一下上面那条命令
Assistant: #CHAT: 那条命令先用 awk 取出每行第一列（客户端 IP），再 sort 排序，uniq -c 统计每个 IP 出现次数，最后 sort -rn 按次数倒序取前 10。
"""

MULTI_TURN_HINT = """You are now in a multi-turn interactive session.

After each command you generate, the user's shell will run it and the execution result
(stdout, stderr, exit code) will be appended to the conversation as a user message
in the following format:

<exec_result>
exit_code: <n>
stdout:
...
stderr:
...
</exec_result>

Use that result as context to answer the user's next request.
Still obey all rules above: output ONLY ONE single-line bash command, OR a #CHAT: reply
when the user is asking a question rather than requesting an action.
"""


# Filter the Texts
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def clean_text(s: str) -> str:
    if not s:
        return s
    s = _SURROGATE_RE.sub("\ufffd", s)
    return s.encode("utf-8", "ignore").decode("utf-8", "ignore")


class LLMError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


# Streaming Filter
class StreamMode:
    UNDECIDED = 0
    CHAT = 1
    COMMAND = 2


class StreamFilter:
    CHAT_MARKER = "#CHAT:"

    def __init__(self):
        self.buf = ""
        self.mode = StreamMode.UNDECIDED
        self.acc = ""
        self.emitted = ""

    def feed(self, chunk):
        if not chunk:
            return ""
        self.acc += chunk

        if self.mode == StreamMode.UNDECIDED:
            self.buf += chunk
            stripped = self.buf.lstrip()

            if stripped.startswith(self.CHAT_MARKER):
                rest = stripped[len(self.CHAT_MARKER):]
                self.mode = StreamMode.CHAT
                self.emitted += rest
                self.buf = ""
                return rest

            if len(stripped) < len(self.CHAT_MARKER) and self.CHAT_MARKER.startswith(stripped):
                return ""

            self.mode = StreamMode.COMMAND
            out = self.buf
            self.buf = ""
            self.emitted += out
            return out

        self.emitted += chunk
        return chunk

    def finish(self):
        if self.mode == StreamMode.UNDECIDED:
            if self.buf.lstrip().startswith(self.CHAT_MARKER):
                self.mode = StreamMode.CHAT
                rest = self.buf.lstrip()[len(self.CHAT_MARKER):]
                self.emitted += rest
                self.buf = ""
                return rest
            self.mode = StreamMode.COMMAND
            out = self.buf
            self.buf = ""
            self.emitted += out
            return out
        return ""


# streaming call
def call_llm_messages_stream(messages, cfg, on_chunk=None, on_mode=None):
    if not cfg["api_key"]:
        raise LLMError("未配置 API Key（WOC_API_KEY 或 ~/.config/woc/config.toml）")

    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            m["content"] = clean_text(c)

    url = cfg["api_base"].rstrip("/") + "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0,
        "max_tokens": 4096,
        "stream": True,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
        method="POST",
    )

    flt = StreamFilter()
    reported_mode = None

    def _emit(text):
        if text and on_chunk:
            on_chunk(text)

    def _maybe_report_mode():
        nonlocal reported_mode
        if flt.mode != StreamMode.UNDECIDED and flt.mode != reported_mode:
            reported_mode = flt.mode
            if on_mode:
                on_mode(flt.mode)

    try:
        resp = urllib.request.urlopen(req, timeout=cfg["timeout"])
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise LLMError(f"HTTP {e.code}: {body}", code=e.code) from e
    except Exception as e:
        raise LLMError(f"请求失败: {e}") from e

    try:
        for raw_line in resp:
            line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue

            try:
                delta = obj["choices"][0].get("delta") or {}
            except (KeyError, IndexError, TypeError):
                continue
            piece = delta.get("content")
            if not piece:
                continue

            out = flt.feed(piece)
            _emit(out)
            _maybe_report_mode()
    except Exception as e:
        raise LLMError(f"流式读取失败: {e}") from e
    finally:
        try:
            resp.close()
        except Exception:
            pass

    tail = flt.finish()
    _emit(tail)
    _maybe_report_mode()

    if not flt.acc.strip():
        raise LLMError("LLM 未返回内容")

    return flt.acc.strip()


def call_llm_messages(messages, cfg, on_chunk=None, on_mode=None):
    return call_llm_messages_stream(messages, cfg, on_chunk=on_chunk, on_mode=on_mode)


def call_llm(user_input, cfg, on_chunk=None, on_mode=None):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_input},
    ]
    return call_llm_messages(messages, cfg, on_chunk=on_chunk, on_mode=on_mode)


# LLM TEXT FILTER
FENCE_LINE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*$|^```\s*$")
PROMPT_RE = re.compile(r"^(\$|>|bash\s+-c)\s*")
CHAT_MARKER = "#CHAT:"


class CommandGenError(Exception):
    pass


class ChatResponse(Exception):
    def __init__(self, text):
        super().__init__(text)
        self.text = text


def sanitize(raw):
    text = raw.strip()

    if text.lstrip().startswith(CHAT_MARKER):
        content = text.lstrip()[len(CHAT_MARKER):].strip()
        raise ChatResponse(content)

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    for line in lines:
        if FENCE_LINE_RE.match(line):
            continue
        if line.startswith(CHAT_MARKER):
            raise ChatResponse(line[len(CHAT_MARKER):].strip())
        if line.startswith("#ERROR"):
            raise CommandGenError(line)
        line = PROMPT_RE.sub("", line)
        line = line.strip("`").strip().strip("'\"")
        if not line:
            continue
        return line

    raise CommandGenError("LLM 未返回有效命令")


DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\s+/(\s|$)",
    r"\bmkfs\b",
    r"\bdd\s+.*of=/dev/[sh]d",
    r":\(\)\s*\{.*\};\s*:",   # fork bomb
    r"\bchmod\s+-R\s+777\s+/(\s|$)",
    r">\s*/dev/[sh]d",
    r"\b(shutdown|reboot|halt|poweroff)\b",
]


def is_dangerous(cmd):
    return any(re.search(p, cmd) for p in DANGEROUS_PATTERNS)


# Execution Commands
def run_command_capture(cmd, max_output=8000, timeout=120):
    try:
        proc = subprocess.run(
            ["bash", "-c", cmd],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        code = proc.returncode
    except subprocess.TimeoutExpired:
        out, err, code = "", f"[woc] 命令超过 {timeout}s 未结束，已终止", 124
    except KeyboardInterrupt:
        out, err, code = "", "[woc] 用户中断 (Ctrl+C)", 130
    except Exception as e:
        out, err, code = "", f"[woc] 执行失败: {e}", 1

    def truncate(s):
        if len(s) <= max_output:
            return s
        return s[:max_output] + f"\n...[truncated {len(s) - max_output} chars]"

    return truncate(out), truncate(err), code


def format_exec_result(out, err, code):
    return (
        "<exec_result>\n"
        f"exit_code: {code}\n"
        "stdout:\n"
        f"{clean_text(out)}\n"
        "stderr:\n"
        f"{clean_text(err)}\n"
        "</exec_result>"
    )


# Single ChatCompletion
def woc_main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print("用法: woc <自然语言描述>")
        print("示例: woc 帮我抓一次实时资源的 snapshot 到 a.txt")
        print("     woc 解释一下 du -sh 的用法")
        sys.exit(0)

    user_input = " ".join(argv)
    cfg = load_config()

    state = {"mode": None}

    def on_mode(mode):
        state["mode"] = mode

    def on_chunk(text):
        if state["mode"] == StreamMode.COMMAND:
            sys.stdout.write(cout(text, C.CYAN, C.BOLD))
        else:
            sys.stdout.write(text)
        sys.stdout.flush()

    try:
        raw = call_llm(user_input, cfg, on_chunk=on_chunk, on_mode=on_mode)
    except LLMError as e:
        eprint(f"[woc] {e}", C.RED)
        sys.exit(3)

    if state["mode"] == StreamMode.COMMAND:
        sys.stdout.write("\n")
        sys.stdout.flush()
        eprint("[woc] 按 Enter 执行，Ctrl+C 取消", C.GREEN)
    elif state["mode"] == StreamMode.CHAT:
        sys.stdout.write("\n")
        sys.stdout.flush()

    try:
        cmd = sanitize(raw)
    except ChatResponse:
        sys.exit(0)
    except CommandGenError as e:
        eprint(f"[woc] {e}", C.RED)
        sys.exit(4)

    if is_dangerous(cmd):
        eprint("[woc] ⚠️  检测到潜在危险命令，已拒绝：", C.YELLOW, C.BOLD)
        eprint(f"  {cmd}", C.YELLOW)
        sys.exit(5)

    try:
        input()
    except (KeyboardInterrupt, EOFError):
        eprint("\n[woc] 已取消", C.YELLOW)
        sys.exit(130)

    os.execvp("bash", ["bash", "-c", cmd])


# Multi ChatCompletion -i
def wocc_main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print("用法: wocc [首轮自然语言描述]")
        print("进入交互式多轮会话，每轮命令的执行结果会自动反馈给 LLM。")
        print("输入 :q / exit / quit 退出。")
        print("提问（如“解释一下”“为什么...”）会自动进入 chat 模式，不会被执行。")
        sys.exit(0)

    cfg = load_config()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": MULTI_TURN_HINT},
    ]

    eprint("[woc] 已进入多轮会话，输入 :q 退出", C.GREEN, C.BOLD)

    pending_input = " ".join(argv) if argv else None

    while True:
        if pending_input is not None:
            user_input = pending_input
            pending_input = None
            eprint(f"> {user_input}", C.MAGENTA)
        else:
            try:
                sys.stderr.write(cerr("> ", C.MAGENTA))
                sys.stderr.flush()
                user_input = input().strip()
            except (KeyboardInterrupt, EOFError):
                eprint("\n[woc] 会话结束", C.GREEN)
                break

        if not user_input:
            continue
        if user_input in (":q", "exit", "quit"):
            eprint("[woc] 会话结束", C.GREEN)
            break

        messages.append({"role": "user", "content": user_input})

        state = {"mode": None}

        def on_mode(mode):
            state["mode"] = mode

        def on_chunk(text):
            if state["mode"] == StreamMode.COMMAND:
                sys.stdout.write(cout(text, C.CYAN, C.BOLD))
            else:
                sys.stdout.write(text)
            sys.stdout.flush()

        try:
            raw = call_llm_messages(
                messages, cfg, on_chunk=on_chunk, on_mode=on_mode
            )
        except LLMError as e:
            eprint(f"[woc] LLM 调用失败：{e}", C.RED)
            eprint("[woc] 本轮已跳过，可换个说法重试，或 :q 退出", C.YELLOW)
            messages.pop()
            continue

        # 命令模式：命令已流完，补换行，再打印提示
        if state["mode"] == StreamMode.COMMAND:
            sys.stdout.write("\n")
            sys.stdout.flush()
            eprint("[woc] 按 Enter 执行，输入 s 跳过，Ctrl+C 取消", C.GREEN)
        elif state["mode"] == StreamMode.CHAT:
            sys.stdout.write("\n")
            sys.stdout.flush()

        try:
            cmd = sanitize(raw)
        except ChatResponse:
            messages.append({"role": "assistant", "content": raw})
            continue
        except CommandGenError as e:
            eprint(f"[woc] {e}", C.RED)
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": (
                    "<exec_result>\n"
                    "exit_code: 1\n"
                    "stdout:\n\n"
                    f"stderr:\n命令生成失败：{clean_text(str(e))}\n"
                    "</exec_result>"
                ),
            })
            continue

        messages.append({"role": "assistant", "content": cmd})

        if is_dangerous(cmd):
            eprint("[woc] ⚠️  检测到潜在危险命令，已拒绝：", C.YELLOW, C.BOLD)
            eprint(f"  {cmd}", C.YELLOW)
            messages.append({
                "role": "user",
                "content": (
                    "<exec_result>\n"
                    "exit_code: 126\n"
                    "stdout:\n\n"
                    "stderr:\n命令因安全策略被拒绝，请换一种安全写法。\n"
                    "</exec_result>"
                ),
            })
            continue

        try:
            ans = input()
        except (KeyboardInterrupt, EOFError):
            eprint("\n[woc] 会话结束", C.GREEN)
            break

        if ans.strip().lower() == "s":
            eprint("[woc] 已跳过", C.YELLOW)
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
            continue

        out, err, code = run_command_capture(cmd)

        if out:
            sys.stdout.write(out)
            if not out.endswith("\n"):
                sys.stdout.write("\n")
        if err:
            sys.stderr.write(err)
            if not err.endswith("\n"):
                sys.stderr.write("\n")

        if code != 0:
            eprint(f"[woc] exit_code={code}", C.RED)

        messages.append({
            "role": "user",
            "content": format_exec_result(out, err, code),
        })


def main():
    prog = os.path.basename(sys.argv[0])
    argv = sys.argv[1:]

    if prog == "wocc":
        wocc_main(argv)
    else:
        woc_main(argv)


if __name__ == "__main__":
    main()
