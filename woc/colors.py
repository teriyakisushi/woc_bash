import os
import sys
import codecs
import shutil
import termios
import tty
import unicodedata
import re
import select
import time


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


ESC = chr(27)

# 会话内历史：只在本进程有效，↑/↓ 回溯；不落盘（落盘是 session 的职责）。
_HISTORY = []
_HISTORY_LIMIT = 200


def _remember(line):
    """记一条历史；空行与紧邻重复不记。"""
    if not line or (_HISTORY and _HISTORY[-1] == line):
        return
    _HISTORY.append(line)
    if len(_HISTORY) > _HISTORY_LIMIT:
        del _HISTORY[0]


def _word_start(text, cursor):
    """Ctrl+W 的删词边界：先跳过尾部空白，再跳到上一个空白之后。"""
    index = min(cursor, len(text))
    while index > 0 and text[index - 1].isspace():
        index -= 1
    while index > 0 and not text[index - 1].isspace():
        index -= 1
    return index


def _drain_pending(fd, limit=65536):
    """丢掉 Enter 之后仍在 tty 缓冲区里的字节，返回丢弃的字节数。

    多行粘贴时，第一行之后的内容会留在缓冲里；下一次 `read_key()` 会把
    它的首字节当成一次确认（任何非 n/e 的键都算执行），所以必须在这里清空。
    粘贴可能分几次到达，丢弃过字节就再等 10ms 清一遍。
    """
    dropped = 0
    for _ in range(3):
        while dropped < limit:
            try:
                ready, _, _ = select.select([fd], [], [], 0)
            except (OSError, ValueError):
                return dropped
            if not ready:
                break
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                return dropped
            if not chunk:
                return dropped
            dropped += len(chunk)
        if not dropped:
            break
        time.sleep(0.01)
    return dropped


def interactive_input(prompt, stream=sys.stderr, initial="", notice=None):
    """Read a line while keeping the fixed prompt outside editable input.

    initial 预填一段文本（用于 `e` 编辑已生成的命令）；管道输入时忽略。
    notice 是可选的提示回调（多行粘贴被截断时用它告诉用户）。
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
    history = list(_HISTORY)
    hist_index = len(history)
    draft = ""
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
            chars[0] = "~"
        if chars and end < len(text):
            chars[-1] = "~"
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
                dropped = _drain_pending(fd)
                line = "".join(text)
                _remember(line)
                stream.write("\n")
                if dropped and notice is not None:
                    notice("粘贴内容有多行，只保留第一行"
                           "（其余 %d 字节已丢弃）" % dropped)
                stream.flush()
                return line
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
                    elif sequence == b"A":
                        if hist_index > 0:
                            if hist_index == len(history):
                                draft = "".join(text)
                            hist_index -= 1
                            text = list(history[hist_index])
                            cursor = len(text)
                    elif sequence == b"B":
                        if hist_index < len(history):
                            hist_index += 1
                            text = (list(history[hist_index])
                                    if hist_index < len(history)
                                    else list(draft))
                            cursor = len(text)
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
            code = char[0]
            if code in (8, 127):
                if cursor:
                    cursor -= 1
                    text.pop(cursor)
            elif code == 1:            # Ctrl+A 行首
                cursor = 0
            elif code == 5:            # Ctrl+E 行尾
                cursor = len(text)
            elif code == 11:           # Ctrl+K 删到行尾
                del text[cursor:]
            elif code == 12:           # Ctrl+L 清屏重绘
                stream.write(ESC + "[2J" + ESC + "[H")
            elif code == 21:           # Ctrl+U 删到行首
                del text[:cursor]
                cursor = 0
            elif code == 23:           # Ctrl+W 删前一个词
                start = _word_start(text, cursor)
                del text[start:cursor]
                cursor = start
            elif code >= 32:           # 其它控制字符不进正文
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
