import re

from .llm import clean_text

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
        line = PROMPT_RE.sub("", line).strip()
        # 不要删除命令两端的引号：它们可能是 shell 语法的一部分。
        if len(line) >= 2 and line.startswith("`") and line.endswith("`"):
            line = line[1:-1].strip()
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

