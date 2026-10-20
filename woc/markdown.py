"""终端 Markdown 渲染（零依赖）。

渲染规则只写一份（`_Renderer`），两个入口共用同一套：

- `render_markdown(text)`：一次性渲染完整文本。
- `MarkdownStream`：边收边渲染，`woc -c` 用它把 SSE 增量立刻显示出来。

表格要看到整块才知道列宽，所以流式渲染会先把表格行攒住，等表格结束
（出现第一行不含 `|` 的行）再整块吐出去；代价只是"表格比正文晚一点出现"，
而不是"整条回答收完才出现"。
"""

import re

from .colors import C
from .llm import clean_text

_FENCE_PREFIX = "```"
_TABLE_START_RE = re.compile(r"^\s*\|?\s*:?-{3,}")
_TABLE_CELL_RE = re.compile(r":?-{3,}:?")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BULLET_RE = re.compile(r"^\s*[-*]\s+")

# 一行还没写完时最多攒多少个字符就先吐一截（见 MarkdownStream.feed）。
HOLD_CHARS = 240


def _looks_like_table_start(line, following):
    """当前行是表头吗？判定条件与旧的整段渲染完全一致。"""
    return "|" in line and bool(_TABLE_START_RE.match(following)) and "|" in following


def _is_separator_row(cells):
    return all(_TABLE_CELL_RE.fullmatch(cell) for cell in cells)


class _Renderer:
    """逐行状态机：`push()` 喂完整行，`flush()` 收尾，渲染结果交给 `emit`。"""

    def __init__(self, colors, emit):
        self.colors = colors
        self.emit = emit
        self.in_code = False
        self.queue = []

    def push(self, line):
        self.queue.append(line)
        self._pump(final=False)

    def flush(self):
        self._pump(final=True)

    def partial(self, text):
        """一行还没结束时，先把能确定的一截渲染出去。

        不参与表格/围栏判定：这一截不是完整行，判定会失真。
        """
        if self.in_code:
            self.emit(self.colors(text, C.CYAN))
        else:
            self.emit(self._inline(text))

    def _pump(self, final):
        while self.queue:
            if not final and not self._ready():
                return
            self._take()

    def _ready(self):
        head = self.queue[0]
        # 表格判定优先于代码围栏，与既有渲染保持一致。
        if "|" not in head:
            return True
        if len(self.queue) < 2:
            return False          # 可能是表头，得看下一行才知道
        if not _looks_like_table_start(head, self.queue[1]):
            return True
        return self._table_end() is not None

    def _table_end(self):
        for index in range(1, len(self.queue)):
            if "|" not in self.queue[index].strip():
                return index
        return None

    def _take(self):
        head = self.queue[0]
        if "|" in head and len(self.queue) >= 2 and \
                _looks_like_table_start(head, self.queue[1]):
            end = self._table_end()
            if end is None:
                end = len(self.queue)         # 表格被截断：有多少渲染多少
            rows = self.queue[:end]
            del self.queue[:end]
            for out in self._table(rows):
                self.emit(out)
            return
        del self.queue[0]
        for out in self._line(head):
            self.emit(out)

    def _line(self, line):
        if line.strip().startswith(_FENCE_PREFIX):
            self.in_code = not self.in_code
            return [] if self.in_code else [""]
        if self.in_code:
            return [self.colors(line, C.CYAN)]
        return [self._inline(line)]

    def _inline(self, line):
        line = _HEADING_RE.sub(
            lambda match: self.colors(match.group(2), C.BOLD, C.BLUE), line)
        line = _BOLD_RE.sub(
            lambda match: self.colors(match.group(1), C.BOLD), line)
        line = _INLINE_CODE_RE.sub(
            lambda match: self.colors(match.group(1), C.YELLOW), line)
        return _BULLET_RE.sub("• ", line)

    def _table(self, rows):
        table = []
        for row in rows:
            cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
            if not _is_separator_row(cells):
                table.append(cells)
        if not table:
            return []
        columns = max(len(row) for row in table)
        table = [row + [""] * (columns - len(row)) for row in table]
        widths = [max(len(row[col]) for row in table) for col in range(columns)]
        border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
        out = [border]
        for index, row in enumerate(table):
            out.append("| " + " | ".join(
                cell.ljust(widths[col]) for col, cell in enumerate(row)) + " |")
            if index == 0:
                out.append(border)
        out.append(border)
        return out


def render_markdown(text: str, colors: C) -> str:
    """一次性渲染完整文本（管道、已经拿到全文的场景）。"""
    rendered = []
    renderer = _Renderer(colors, rendered.append)
    for line in (clean_text(text) or "").splitlines():
        renderer.push(line)
    renderer.flush()
    return "\n".join(rendered)


def _split_hold(text, limit):
    """把一行切一刀，尽量不切在 `**` / 反引号 token 中间。"""
    cut = text.rfind(" ", 0, limit)
    if cut <= 0:
        cut = limit
    for token in ("**", "`"):
        head = text[:cut]
        if head.count(token) % 2 == 1:            # 断在 token 里了，往回退
            opening = head.rfind(token)
            if opening > 0:
                cut = opening
    return text[:cut], text[cut:]


class MarkdownStream:
    """边收边渲染：`feed()` 喂增量，`finish()` 收尾。

    `write` 每拿到一行（或一截行）就已经能确定的内容调一次，**不带换行**，
    由调用方决定怎么输出。`sanitize` 用来在显示前过滤一行里的控制标记
    （`woc -c` 传 `strip_dsml`）：因为整行都先攒着，标记不会半截漏出去。
    """

    def __init__(self, colors, write=None, sanitize=None):
        self.write = write or (lambda text: None)
        self.sanitize = sanitize
        self._pending = ""
        self._renderer = _Renderer(colors, self.write)

    def feed(self, chunk):
        chunk = clean_text(chunk or "")
        if not chunk:
            return
        self._pending += chunk
        while True:
            cut = self._pending.find("\n")
            if cut >= 0:
                line = self._pending[:cut]
                self._pending = self._pending[cut + 1:]
                self._renderer.push(self._clean(line))
                continue
            if len(self._pending) <= HOLD_CHARS:
                return
            # 一行迟迟不结束（模型写长段落）：先吐一截，别等整条回答收完。
            head, self._pending = _split_hold(self._pending, HOLD_CHARS)
            self._renderer.partial(self._clean(head))

    def finish(self):
        if self._pending:
            self._renderer.push(self._clean(self._pending))
            self._pending = ""
        self._renderer.flush()

    def _clean(self, line):
        if line.endswith("\r"):
            line = line[:-1]
        return self.sanitize(line) if self.sanitize else line
