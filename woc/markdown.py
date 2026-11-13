"""终端 Markdown 渲染（零依赖）。

渲染规则只写一份（`_Renderer`），两个入口共用同一套：

- `render_markdown(text)`：一次性渲染完整文本。
- `MarkdownStream`：边收边渲染，`woc -c` 用它把 SSE 增量立刻显示出来。

表格要看到整块才知道列宽，所以流式渲染会先把表格行攒住，等表格结束
（出现第一行不含 `|` 的行）再整块吐出去；代价只是"表格比正文晚一点出现"，
而不是"整条回答收完才出现"。

流式上屏按"安全前缀"切：终端是只追加的，吐出去的字擦不掉，所以
`MarkdownStream` 只吐"怎么切都不会改变最终画面"的部分（散文正文逐块吐），
整行形态的构造——标题、围栏、代码、表格行——一律留到换行再渲染。
`tests/test_markdown.py` 用不同切块大小要求流式与一次性渲染逐字节一致，
守的正是这条线。
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

# 一行还没写完时，一次最多先吐这么多字符（见 MarkdownStream._flushable）。
HOLD_CHARS = 240
# 能吐的前缀短于这个长度就先不吐，免得为几个字符写一次 stdout。
FLUSH_CHARS = 8


def _looks_like_table_start(line, following):
    """当前行是表头吗？判定条件与旧的整段渲染完全一致。"""
    return "|" in line and bool(_TABLE_START_RE.match(following)) and "|" in following


def _is_separator_row(cells):
    return all(_TABLE_CELL_RE.fullmatch(cell) for cell in cells)


class _Renderer:
    """逐行状态机：`push()` 喂完整行，`flush()` 收尾，渲染结果交给 `emit`。"""

    def __init__(self, colors, emit, emit_partial=None):
        self.colors = colors
        self.emit = emit                            # 完整行（不带换行）
        self.emit_partial = emit_partial or emit     # 还没写完的行里的一截
        self.in_code = False
        self.queue = []

    def push(self, line, continuation=False):
        """喂一行。`continuation`：这行的开头已经先吐过了，只剩尾巴。"""
        if continuation:
            # 不能走 `_line()`：标题/围栏/列表符号都只认行首，尾巴不是行首。
            self.emit(self._inline(line, line_start=False))
            return
        self.queue.append(line)
        self._pump(final=False)

    def flush(self):
        self._pump(final=True)

    def render(self, text, line_start=True):
        """按“先吐这一截”的方式渲染但不输出（切割前用它自检）。"""
        if self.in_code:
            return self.colors(text, C.CYAN)
        return self._inline(text, line_start=line_start)

    def partial(self, text, line_start=True):
        """一行还没结束时，先把能确定的一截渲染出去。

        不参与表格/围栏判定：这一截不是完整行，判定会失真。`line_start` 为假
        表示前面已经吐过一截，不能把 `- ` / `# ` 当行首构造再替换一次。
        """
        self.emit_partial(self.render(text, line_start=line_start))

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

    def _inline(self, line, line_start=True):
        if line_start:
            line = _HEADING_RE.sub(
                lambda match: self.colors(match.group(2), C.BOLD, C.BLUE), line)
        line = _BOLD_RE.sub(
            lambda match: self.colors(match.group(1), C.BOLD), line)
        line = _INLINE_CODE_RE.sub(
            lambda match: self.colors(match.group(1), C.YELLOW), line)
        if line_start:
            line = _BULLET_RE.sub("• ", line)
        return line

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


def _token_spans(text):
    """`_inline` 会整段替换的片段（`**粗体**`、`` `代码` ``）的起止下标。"""
    spans = []
    for pattern in (_BOLD_RE, _INLINE_CODE_RE):
        spans.extend((match.start(), match.end())
                     for match in pattern.finditer(text))
    return spans


def _cut_candidates(text):
    """能先上屏的切点，从长到短。

    `_inline` 的替换按 token 整段发生，所以切点只能落在 token 边界上（或者还
    没有 token 的地方）；切在 token 里面，先吐的那截就会留下字面 `**` / 反引号。
    """
    spans = _token_spans(text)
    limit = min(len(text), HOLD_CHARS)
    cuts = {limit} | {end for _start, end in spans if end <= limit}
    for index, char in enumerate(text[:limit]):
        if char not in "*`":
            continue
        if not any(start <= index < end for start, end in spans):
            cuts.add(index)                  # 孤立/未闭合的 token 之前
            break
    return sorted(cuts, reverse=True)


class MarkdownStream:
    """边收边渲染：`feed()` 喂增量，`finish()` 收尾。

    `write` 拿到的就是该上屏的字节：完整行已经带换行，一行还没写完的半截没有。
    `sanitize` 用来在显示前过滤控制标记（`woc -c` 传 `strip_dsml`）：切割点永远
    不在 `<` 之后，所以标记不会半截漏出去。
    """

    def __init__(self, colors, write=None, sanitize=None):
        self.write = write or (lambda text: None)
        self.sanitize = sanitize
        self._pending = ""
        self._partial = False           # 当前这行已经先吐过一截
        self._renderer = _Renderer(colors, self._write_line, self.write)

    def _write_line(self, line):
        self.write(line + "\n")

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
                self._renderer.push(
                    self._clean(line), continuation=self._partial)
                self._partial = False
                continue
            head = self._flushable()
            if not head:
                return
            self._pending = self._pending[len(head):]
            self._renderer.partial(
                self._clean(head), line_start=not self._partial)
            self._partial = True

    def _flushable(self):
        """这一行里能先上屏的前缀；返回空串表示还不到时候。

        只切在"怎么切都不改变最终画面"的位置：整行形态的构造全部留到换行，
        `_inline` 里逐 token 的替换则允许分块（拼接结果与整行渲染相同）。
        """
        text = self._pending
        if not text or self._renderer.in_code:
            return ""                       # 代码行整行渲染，颜色序列才一致
        if self._renderer.queue:
            return ""                       # 前面还有没上屏的行（表格等），先吐会乱序
        if "|" in text or "<" in text:
            return ""                       # 表格行列宽要整块；控制标记不能被切开
        line_start = not self._partial   # 这一截前面还没有吐过东西
        if line_start:                   # 下面这些只对行首有意义
            lead = text.lstrip()
            if not lead:
                return ""                   # 只有空白，后面可能接标题/围栏
            if _FENCE_PREFIX in lead or lead.strip("`") == "":
                return ""                   # 围栏行，或者还看不出是不是围栏
            hashes = len(lead) - len(lead.lstrip("#"))
            if hashes:
                rest = lead[hashes:]
                if not rest or rest[:1] in (" ", "\t"):
                    return ""               # 标题整行留着：`_HEADING_RE` 只认行首
            if lead[:1] in ("-", "*") and not _BULLET_RE.match(text):
                return ""                   # 列表符号还没长全（也可能是 `**` 的开头）
        for cut in _cut_candidates(text):
            if cut < FLUSH_CHARS:
                break
            if self._renders_clean(text[:cut], line_start):
                return text[:cut]
        return ""

    def _renders_clean(self, head, line_start):
        """这截单独渲染，会不会留下半截标记（字面 `*` / 反引号）。"""
        rendered = self._renderer.render(head, line_start=line_start)
        return "*" not in rendered and "`" not in rendered

    def finish(self):
        if self._pending:
            self._renderer.push(
                self._clean(self._pending), continuation=self._partial)
            self._pending = ""
            self._partial = False
        elif self._partial:
            self.write("\n")            # 这行是分块吐完的，得补上它自己的换行
            self._partial = False
        self._renderer.flush()

    def _clean(self, line):
        if line.endswith("\r"):
            line = line[:-1]
        return self.sanitize(line) if self.sanitize else line
