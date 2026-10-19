import re

from .llm import clean_text
# 危险命令判定在 gate.py；这里 re-export，保持现有调用方不变。
from .gate import dangerous_reason, is_dangerous

# LLM TEXT FILTER
FENCE_LINE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*$|^```\s*$")

# DeepSeek 的 DSML 控制标记（工具调用等），例如：
#   <｜｜DSML｜｜function_calls> ... </｜｜DSML｜｜function_calls>
# 兼容全角 ｜｜ 与半角 ||，以及不带名字的 <｜｜DSML｜｜>。
# 这些标记不是命令，绝不能进 bash。
_DSML_BARS = r"(?:｜｜|\|\|)"
_DSML_OPEN = r"<\s*" + _DSML_BARS + r"\s*DSML\s*" + _DSML_BARS + r"[^>]*>"
_DSML_CLOSE = r"<\s*/\s*" + _DSML_BARS + r"\s*DSML\s*" + _DSML_BARS + r"[^>]*>"
_DSML_BLOCK_RE = re.compile(_DSML_OPEN + r".*?" + _DSML_CLOSE, re.S)
_DSML_TAG_RE = re.compile(r"<\s*/?\s*" + _DSML_BARS + r"\s*DSML\s*" + _DSML_BARS + r"[^>]*>")
# 流被截断或模型少写 `>` 时的残片：从标记开头删到行尾
_DSML_LOOSE_RE = re.compile(
    r"<\s*/?\s*" + _DSML_BARS + r"\s*DSML\s*" + _DSML_BARS + r"[^>\n]*(?:>|$)",
    re.M,
)
# 最坏情况：连尖括号都没有
_DSML_BARE_RE = re.compile(_DSML_BARS + r"\s*DSML\s*" + _DSML_BARS)

# 工具调用/标签残留：模型偶尔会把 <exec_result> 当工具来调用。
# 这些不是命令，绝不能把里面的 placeholder 当命令行执行。
_TOOLCALL_RE = re.compile(
    r"(?:\|\|\s*DSML\s*\|\||｜｜\s*DSML\s*｜｜"
    r"|</?exec_result\b|<exec_result\b"
    r"|</?function_calls?\b|<invoke\b|</?tool_calls?\b)"
)
# 整行就是一个 XML 标签（例如 </br>、</exec_result>），不是命令。
_TAG_LINE_RE = re.compile(r"^</?[A-Za-z][A-Za-z0-9_.:-]*(?:\s[^>]*)?>$")


def has_toolcall(text):
    """原始模型输出里是否含工具调用/标签残留（在 strip_dsml 之前检查）。"""
    return bool(text and _TOOLCALL_RE.search(text))


def strip_dsml(text):
    """去掉模型漏出来的 DSML 控制标记。

    顺序：整块 → 完整标签 → 行尾残片 → 裸标记。这些都是控制 token，不是命令。
    """
    if not text:
        return text
    text = _DSML_BLOCK_RE.sub("", text)
    text = _DSML_TAG_RE.sub("", text)
    text = _DSML_LOOSE_RE.sub("", text)
    return _DSML_BARE_RE.sub("", text)
PROMPT_RE = re.compile(r"^(\$|>|bash\s+-c)\s*")
CHAT_MARKER = "#CHAT:"
# 纯变量赋值行：`NAME=...`，且后面没有跟着命令（`NAME=x cmd` 是有输出的，不算）
ASSIGN_ONLY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# 引号 / 括号里的空白不属于"命令分隔空白"
_CLOSERS = {"(": ")", "[": "]", "{": "}"}


def _has_trailing_words(rest):
    """`NAME=` 之后的部分是否还跟着一条命令。

    `$(find . -type f)` → False（括号内的空格属于值本身）
    `"a b"`             → False（引号内的空格属于值本身）
    `1 ls`              → True （`ls` 是真正的命令）
    扫描时跳成对的括号与引号，只看剩下的裸空白。
    """
    stack = []
    quote = None
    index = 0
    while index < len(rest):
        char = rest[index]
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in _CLOSERS:
            stack.append(_CLOSERS[char])
        elif stack and char == stack[-1]:
            stack.pop()
        elif not stack and char.isspace():
            return True
        index += 1
    return False


def is_assignment_only(line):
    """判断是否为"只赋值、不产出"的行。

    `f=$(find ...)` 返回 True；`f=$(find ...); echo "$f"`、`x=1 ls` 返回 False。
    """
    if not ASSIGN_ONLY_RE.match(line):
        return False
    name_end = line.index("=")
    return not _has_trailing_words(line[name_end + 1:])


class CommandGenError(Exception):
    pass


class ChatResponse(Exception):
    def __init__(self, text):
        super().__init__(text)
        self.text = text


def sanitize(raw):
    text = strip_dsml(raw).strip()

    if text.lstrip().startswith(CHAT_MARKER):
        content = text.lstrip()[len(CHAT_MARKER):].strip()
        raise ChatResponse(content)

    # 工具调用残留不是命令：宁可报错让模型重写，也不能执行 placeholder。
    if _TOOLCALL_RE.search(raw) or _TOOLCALL_RE.search(text):
        raise CommandGenError(
            "模型输出了工具调用/标签格式，已拒绝执行；"
            "请只返回一条命令，或 #CHAT: 回答")

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # 排除围栏行后剩下的内容行 —— 用于判断"是不是还有后续行被丢掉了"
    body_indexes = [i for i, l in enumerate(lines) if not FENCE_LINE_RE.match(l)]
    first_body = body_indexes[0] if body_indexes else -1

    for index, line in enumerate(lines):
        if FENCE_LINE_RE.match(line):
            continue
        if _TAG_LINE_RE.match(line):
            raise CommandGenError(
                "模型输出了 XML/标签行，已拒绝执行；请只返回一条命令")
        if line.startswith(CHAT_MARKER):
            raise ChatResponse(line[len(CHAT_MARKER):].strip())
        if line.startswith("#ERROR"):
            raise CommandGenError(line)
        # 注释行不是命令。模型在命令前后写说明时，绝不能把注释当命令执行。
        if line.startswith("#"):
            continue
        line = PROMPT_RE.sub("", line).strip()
        # 不要删除命令两端的引号：它们可能是 shell 语法的一部分。
        if len(line) >= 2 and line.startswith("`") and line.endswith("`"):
            line = line[1:-1].strip()
        if not line:
            continue

        # 多行输出的截断防护：如果这是纯赋值行、且后面还有别的行，那条赋值既不会
        # 产出输出，赋的变量也不会被后面的行用到（每次执行都是独立的 bash -c）。
        # 直接报错让模型重写，好过静默执行一条什么都没干的命令。
        if len(body_indexes) > 1 and index == first_body and is_assignment_only(line):
            remaining = len(body_indexes) - 1
            raise CommandGenError(
                f"命令不完整：返回了 {len(body_indexes)} 行，第一行只是变量赋值"
                f"（{line[:50]}），另有 {remaining} 行无法生效。"
                "请合并成一条单行命令，例如 f=$(...); echo \"$f\"; cat \"$f\""
            )
        return line

    raise CommandGenError("LLM 未返回有效命令")


# 会打开交互式 root shell 的命令。在 wocc 的捕获路径里会挂住；在单轮模式里
# 等于把终端交给一个不受 woc 约束的 root shell，因此两种入口都直接拒绝。
# 注意只认"命令位置"上的 su：`grep su` 这类参数不该被误判。
ROOT_SHELL_PATTERNS = [
    # sudo -i / -s / --login / --shell（含组合短选项，如 -si、-H -i）
    re.compile(r"\bsudo\b[^;&|]*\s(?:-[A-Za-z]*[is][A-Za-z]*|--login\b|--shell\b)"),
    # 裸 su、sudo su、exec su ...
    re.compile(r"(?:^|[;&|]\s*|\bsudo\s+|\bexec\s+|\btime\s+)su(?:\s|$)"),
]


def is_root_shell(cmd):
    return any(re.search(p, cmd) for p in ROOT_SHELL_PATTERNS)
