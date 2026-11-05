"""管道模式：标准输入不是终端时，把 stdin 当数据，只问答、不执行。

设计约束（改动前先读）：

1. 本模块刻意**不导入** exec / gate / sanitize 的执行路径。管道模式没有命令执行，
   “每条命令都要人工确认”在这里是结构性成立，而不是靠运行时判断。
2. stdin 内容只进 user 消息，绝不进 system 段：system 前缀必须逐字节稳定，
   否则前缀缓存每次调用都会失效（见 context.py 顶部说明）。
3. 输入有上限（[pipe] max_chars）。超限时保留头 60% + 尾 40% —— 日志的头有上下文、
   尾有崩溃现场 —— 并显式告诉模型“内容被截断了”。
4. 答案走 stdout（可以继续 `| less` / `> out.md`），提示走 stderr；
   颜色由 stream 是否为 tty 自动判定，不需要额外开关。
5. `format` 只报无歧义的两类（diff / JSON）。**不猜 Markdown**：行首 `#` 既是标题，
   也是 shell 注释与日志前缀，猜错比不猜更糟 —— 提示词里直接告诉模型"没标注就按
   它看起来的样子读"。
"""

import codecs
import json
import os
import re
import sys

from . import debug
from .colors import C, eprint
from .config import pipe_max_chars, prefix, spinner as spinner_enabled
from .context import render_environment
from .llm import LLMError, call_llm_messages
from .prompts import PIPE_SYSTEM_PROMPT
from .ui import Spinner

# ANSI 转义：`ls --color=always` 之类的内容既占 token 又干扰模型读日志。
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

# 前 4KB 出现 NUL 就当二进制拒掉：模型读不了，硬塞进去只会浪费 token。
_BINARY_PROBE = 4096

# 截断时的头尾比例：头 60%、尾 40%。
_HEAD_RATIO = 0.6

# 没有给问题时用这句。刻意不预设“这是日志”：stdin 也可能是 README、源码或 diff。
DEFAULT_QUESTION = (
    "概括/解释上面这段内容的要点：如果是日志或命令输出，指出报错、异常与下一步；"
    "如果是文档或代码，说明它讲什么、结构如何、哪里值得注意。"
    "如果这段内容其实没什么可分析的（打招呼、道谢、闲聊、讲了个笑话），"
    "就顺着用户的话头聊两句，语气轻松一点。"
)


class PipeInputError(Exception):
    """stdin 不能当作给模型看的文本。"""


_DIFF_GIT_RE = re.compile(r"^diff --git ")
_DIFF_HUNK_RE = re.compile(r"^@@ -[0-9]")


def detect_format(text):
    """尽力识别输入格式，作为提示交给模型（识别不了就返回空串）。

    只认两种无歧义的：unified diff 和 JSON。日志/文本的启发式太容易误判，
    宁可不说，也不给模型错误先验。
    """
    head = text[:4000]
    for line in head.splitlines():
        if _DIFF_GIT_RE.match(line) or _DIFF_HUNK_RE.match(line):
            return "diff"
    if head.lstrip()[:1] in ("{", "["):
        try:
            json.loads(text)
        except ValueError:
            return ""
        return "json"
    return ""


def split_max_chars(argv):
    """摘出 `--max-chars N` / `--max-chars=N`，返回 (剩余参数, 覆盖值)。"""
    args = []
    override = None
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--max-chars":
            if index + 1 >= len(argv):
                raise ValueError("--max-chars 需要一个正整数")
            override = _positive_int(argv[index + 1])
            index += 2
            continue
        if item.startswith("--max-chars="):
            override = _positive_int(item.split("=", 1)[1])
            index += 1
            continue
        args.append(item)
        index += 1
    return args, override


def _positive_int(raw):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("--max-chars 需要一个正整数: %r" % (raw,))
    if value <= 0:
        raise ValueError("--max-chars 需要一个正整数: %r" % (raw,))
    return value


def truncate_middle(text, limit):
    """保留头 60% + 尾 40%，中间插显式标记。返回 (text, removed_chars)。"""
    if len(text) <= limit:
        return text, 0
    head_len = max(int(limit * _HEAD_RATIO), 1)
    tail_len = max(limit - head_len, 1)
    head = text[:head_len]
    tail = text[-tail_len:]
    # 尽量停在换行处，别把半行日志交给模型
    cut = head.rfind("\n")
    if cut >= head_len // 2:
        head = head[:cut]
    newline = tail.find("\n")
    # 尾部从下一行开始，避免第一行只剩半截（真机抽样里模型会把它当成“数据被切断”）
    if newline >= 0:
        tail = tail[newline + 1:]
    removed = len(text) - len(head) - len(tail)
    marker = f"\n... [中间 {removed} 字符已截断] ...\n"
    return head + marker + tail, removed


def _decode(raw, hit_limit):
    """UTF-8 容错解码。命中读取上限时不产生尾部的 U+FFFD —— 那只是半个字符。"""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    return decoder.decode(raw, final=not hit_limit)


def read_stdin(max_chars):
    """读 stdin，返回 (text, truncated)。

    只读 `max_chars * 4 + 1` 字节：UTF-8 最坏 4 字节/字符，这既保证解码后的字符数
    不会远超上限，也不会把 GB 级的输入读进内存。
    """
    limit_bytes = max_chars * 4 + 1
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is not None:
        raw = buffer.read(limit_bytes)
    else:
        raw = sys.stdin.read(limit_bytes).encode("utf-8", "replace")

    if b"\x00" in raw[:_BINARY_PROBE]:
        raise PipeInputError("标准输入看起来是二进制数据（含 NUL 字节），已拒绝")

    hit_limit = len(raw) >= limit_bytes
    text = _ANSI_RE.sub("", _decode(raw, hit_limit))
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")

    if len(text) > max_chars:
        text, _removed = truncate_middle(text, max_chars)
        return text, True
    if hit_limit:
        # 字节上限先到（多字节字符居多）：内容可能没读完，必须显式标注
        return text + "\n... [输入超过读取上限，后续内容未读取] ...", True
    return text, False


def render_stdin_block(text, truncated, fmt=""):
    """把数据包成 <stdin> 块：模型靠标签知道“这是数据，不是指令”。"""
    attrs = ' truncated="true"' if truncated else ""
    if fmt:
        attrs += f' format="{fmt}"'
    return f"<stdin{attrs}>\n{text}\n</stdin>"


def build_pipe_messages(question, data, truncated=False, alias="woc",
                        cwd=None, fmt=""):
    """管道模式的消息：稳定 system 前缀 + 一段数据 + 问题。

    `<environment>` 里那句“命令执行前会有人工确认”在管道模式下不成立，
    所以用 confirm_hint=False 去掉它（见 context.render_environment）。
    """
    return [
        {"role": "system",
         "content": PIPE_SYSTEM_PROMPT.replace("{name}", alias)},
        {"role": "system",
         "content": render_environment(cwd=cwd, confirm_hint=False)},
        {"role": "user",
         "content": render_stdin_block(data, truncated, fmt) + "\\n\\n" + question},
    ]


def _write(text):
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except BrokenPipeError:
        _exit_broken_pipe()


def _exit_broken_pipe():
    """下游（head / less）提前退出：安静收场，别让 Python 打 traceback。"""
    try:
        fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(fd, sys.stdout.fileno())
        os.close(fd)
    except (OSError, ValueError, AttributeError):
        pass
    os._exit(141)


def run_pipe_mode(argv, cfg):
    """执行一次管道问答，返回退出码（不抛 SystemExit，由调用方决定）。"""
    alias = prefix(cfg, "alias")
    name = prefix(cfg, "woc_prefix")
    try:
        args, override = split_max_chars(argv)
    except ValueError as exc:
        eprint(f"[{alias}] {exc}", C.RED)
        return 2
    question = " ".join(args).strip() or DEFAULT_QUESTION
    # system 段是要预留的：预算算的是"还能塞多少数据"，不是总量。
    reserved = (len(PIPE_SYSTEM_PROMPT)
                + len(render_environment(confirm_hint=False)))
    limit = pipe_max_chars(cfg, reserved_chars=reserved)
    max_chars = min(override, limit) if override is not None else limit
    debug.log("pipe: max_chars=%d (budget=%d override=%s) reserved=%d chars"
              % (max_chars, limit, override, reserved))

    try:
        data, truncated = read_stdin(max_chars)
    except KeyboardInterrupt:
        eprint(f"\n[{alias}] 已取消", C.YELLOW)
        return 130
    except PipeInputError as exc:
        eprint(f"[{alias}] {exc}。", C.RED)
        return 2
    fmt = detect_format(data)
    debug.log("pipe: stdin=%d chars truncated=%s format=%s"
              % (len(data), truncated, fmt or "text"))
    if truncated:
        eprint(f"[{alias}] 输入超过 {max_chars} 字符，已截断（只分析保留的部分）",
               C.YELLOW)
    if not data.strip():
        eprint(f"[{alias}] 标准输入为空，没有可分析的内容。", C.RED)
        eprint(f"[{alias}] 用法: <命令> | {name} \"你的问题\"", C.GREEN)
        return 2

    messages = build_pipe_messages(question, data, truncated=truncated,
                                   alias=alias, fmt=fmt)
    state = {"started": False}
    spin = Spinner(f"[{alias}] Thinking", enabled=spinner_enabled(cfg))

    def on_chunk(chunk):
        if not state["started"]:
            # `#CHAT: ` 标记后的空格不该出现在答案开头（重定向到文件时尤其明显）
            chunk = chunk.lstrip()
            if not chunk:
                return
            state["started"] = True
            spin.stop()
        _write(chunk)

    spin.start()
    try:
        answer = call_llm_messages(messages, cfg, on_chunk=on_chunk)
    except KeyboardInterrupt:
        eprint(f"\n[{alias}] 已取消", C.YELLOW)
        return 130
    except LLMError as exc:
        eprint(f"[{alias}] {exc}", C.RED)
        return 3
    finally:
        spin.stop()

    if not state["started"]:
        # 兜底：后端没有通过 on_chunk 回传时，至少别把答案吞掉。
        _write(answer)
    if not answer.endswith("\n"):
        _write("\n")
    return 0
