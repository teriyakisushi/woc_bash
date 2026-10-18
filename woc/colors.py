import os
import sys
import codecs
import shutil
import termios
import tty
import unicodedata
import re


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


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


def _display_width(text):
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in "WF" else 1
    return width


def _visible_width(text):
    return _display_width(_ANSI_ESCAPE.sub("", text))


def interactive_input(prompt, stream=sys.stderr, initial=""):
    """Read a line while keeping the fixed prompt outside editable input.

    initial 预填一段文本（用于 `e` 编辑已生成的命令）；管道输入时忽略。
    """
    if not prompt.startswith("\n"):
        prompt = "\n" + prompt

    stdin = sys.stdin
    if not stdin.isatty():
        reconfigure = getattr(stdin, "reconfigure", None)
        if reconfigure is not None:
            # 管道输入时第二次进入会抛 UnsupportedOperation（读到一半不能再改编码），
            # 这里只是尽力让非法字节不炸，失败了照常读。
            try:
                reconfigure(errors="replace")
            except (OSError, ValueError):
                pass
        return input(prompt)

    # GNU readline can count a CJK character as two separate erase units.
    # Redraw the complete line instead, so Backspace always removes one
    # Unicode code point and can never reach the prompt.
    stream.write(prompt)
    stream.flush()
    redraw_prompt = prompt.lstrip("\n")
    fd = stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    decoder = codecs.getincrementaldecoder(stdin.encoding or "utf-8")(
        errors="replace"
    )
    text = list(initial)
    cursor = len(text)
    escape = 0
    escape_buffer = bytearray()
    columns = max(shutil.get_terminal_size((80, 24)).columns, 8)

    def redraw():
        prompt_width = _visible_width(redraw_prompt)
        available = max(columns - prompt_width - 1, 1)
        start = cursor
        while start > 0 and _display_width("".join(text[start - 1:cursor])) <= available:
            start -= 1
        end = cursor
        while end < len(text) and _display_width("".join(text[start:end + 1])) <= available:
            end += 1
        chars = text[start:end]
        # 单行窗口装不下时，用 < > 标出"两边还有内容"（只占 1 列，不破坏对齐）。
        if chars and start > 0:
            chars[0] = "<"
        if chars and end < len(text):
            chars[-1] = ">"
        visible = "".join(chars)
        suffix_width = _display_width("".join(text[cursor:end]))
        stream.write("\r" + redraw_prompt + visible + "\x1b[K")
        if suffix_width:
            stream.write(f"\x1b[{suffix_width}D")
        stream.flush()

    try:
        tty.setraw(fd)
        while True:
            char = os.read(fd, 1)
            if not char:
                raise EOFError
            if char == b"\x07":  # Ctrl+G：把完整输入打到上面，方便看被滚掉的前文
                stream.write("\r\n" + "".join(text) + "\r\n")
                redraw()
                continue
            if char in (b"\r", b"\n"):
                stream.write("\n")
                stream.flush()
                return "".join(text)
            if char in (b"\x03",):
                raise KeyboardInterrupt
            if char in (b"\x04",):
                if not text:
                    raise EOFError
                continue
            if escape == 1:
                if char == b"[":
                    escape = 2
                    escape_buffer.clear()
                else:
                    escape = 0
                continue
            if escape == 2:
                escape_buffer.extend(char)
                if b"@" <= char <= b"~":
                    sequence = bytes(escape_buffer)
                    if sequence == b"D" and cursor:
                        cursor -= 1
                    elif sequence == b"C" and cursor < len(text):
                        cursor += 1
                    elif sequence in (b"H", b"1~"):
                        cursor = 0
                    elif sequence in (b"F", b"4~"):
                        cursor = len(text)
                    elif sequence == b"3~" and cursor < len(text):
                        text.pop(cursor)
                    escape = 0
                    redraw()
                continue
            if char == b"\x1b":
                escape = 1
                continue
            if char in (b"\x08", b"\x7f"):
                if cursor:
                    cursor -= 1
                    text.pop(cursor)
            else:
                decoded = decoder.decode(char)
                if decoded:
                    text[cursor:cursor] = decoded
                    cursor += len(decoded)
            redraw()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def read_key(prompt="", stream=sys.stderr):
    """读一个按键（tty）或一行（管道），返回小写字符串；Enter 返回空串。"""
    if not sys.stdin.isatty():
        try:
            return input(prompt).strip().lower()
        except EOFError:
            return ""
    stream.write(prompt)
    stream.flush()
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        data = os.read(fd, 1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    if not data:
        raise EOFError
    if data == b"\x03":
        raise KeyboardInterrupt
    if data in (b"\r", b"\n"):
        return ""
    return data.decode("utf-8", "replace").lower()


def eprint(text="", *styles):
    """打印到 stderr，自动换行 + flush。"""
    sys.stderr.write(cerr(text, *styles) + "\n")
    sys.stderr.flush()
