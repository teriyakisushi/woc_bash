"""Markdown 渲染：一次性渲染与流式渲染必须逐字节一致。

`woc -c` 从"攒完再渲染"改成"边收边渲染"后，两条路径共用同一个 `_Renderer`。
这里守住的是：同一段文本，不管切成多大的块喂进去，屏幕上看到的东西必须一样。
"""

import io
import os
import sys
import unittest
from contextlib import contextmanager
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from woc.colors import C
from woc.markdown import HOLD_CHARS, MarkdownStream, render_markdown

SAMPLES = [
    "",
    "hi",
    "hello\n",
    "# heading\n## 二级 **粗体**\n",
    "| a | b |\n|---|---|\n| 1 | 2 |\n| 333 | 4 |\n",
    "| a | b |\n|---|---|\n| 1 | 2 |\n",
    "| a | b |\n|---|---|\n| 1 | 2 |\ntail text\n",
    "text\n| a | b |\n|---|---|\n| 1 | 2 |\n",
    "trailing table no end\n| a | b |\n|---|---|\n| 1 | 2 |",
    "a | b\nnot a table\n",
    "```bash\nls -al | grep x\n```\n",
    "```\ncode\n",
    "- item **bold** and `code`\n* 星号列表\n",
    "line with **unclosed bold\n",
    "header\r\nrow1\r\n\r\n",
    "\n\n\n",
]


class _Tty(io.StringIO):
    def isatty(self):
        return True


@contextmanager
def forced_colors():
    """强制开启 ANSI，让颜色分支也参与对比（不受用户 NO_COLOR 影响）。"""
    with mock.patch.dict(os.environ, {"WOC_FORCE_COLOR": "1"}):
        os.environ.pop("NO_COLOR", None)
        yield


def stream_all(text, colors, size=3, sanitize=None):
    out = []
    stream = MarkdownStream(colors, out.append, sanitize=sanitize)
    for start in range(0, len(text), size):
        stream.feed(text[start:start + size])
    stream.finish()
    return "\n".join(out)


class RenderTest(unittest.TestCase):
    def test_constructs(self):
        with forced_colors():
            colors = C(_Tty())
            self.assertEqual(render_markdown("# 标题", colors),
                             "\033[1m\033[34m标题\033[0m")
            self.assertEqual(render_markdown("- a **b** `c`", colors),
                             "• a \033[1mb\033[0m \033[33mc\033[0m")
            self.assertEqual(
                render_markdown("```\nx\n```", colors),
                "\033[36mx\033[0m\n")

    def test_table(self):
        self.assertEqual(
            render_markdown("| a | b |\n|---|---|\n| 1 | 2 |", C(io.StringIO())),
            "+---+---+\n| a | b |\n+---+---+\n| 1 | 2 |\n+---+---+",
        )

    def test_stream_matches_one_shot(self):
        for colored in (False, True):
            context = forced_colors() if colored else contextmanager(lambda: (yield))()
            with context:
                colors = C(_Tty()) if colored else C(io.StringIO())
                for sample in SAMPLES:
                    expected = render_markdown(sample, colors)
                    for size in (1, 2, 3, 7, 13, HOLD_CHARS, 1000):
                        self.assertEqual(
                            stream_all(sample, colors, size=size),
                            expected,
                            "sample=%r size=%d colored=%s"
                            % (sample, size, colored))


class StreamTest(unittest.TestCase):
    def setUp(self):
        self.colors = C(io.StringIO())
        self.out = []
        self.stream = MarkdownStream(self.colors, self.out.append)

    def test_text_appears_at_once(self):
        self.stream.feed("第一行")
        self.assertEqual(self.out, [])          # 行还没写完，先攒着
        self.stream.feed("\n")
        self.assertEqual(self.out, ["第一行"])   # 换行一到立刻上屏
        self.stream.finish()
        self.assertEqual(self.out, ["第一行"])

    def test_table_held_until_complete(self):
        self.stream.feed("| a | b |\n")
        self.stream.feed("|---|---|\n")
        self.stream.feed("| 1 | 2 |\n")
        self.assertEqual(self.out, [])          # 表格要整块才知道列宽
        self.stream.feed("\n")
        self.assertEqual(self.out, [
            "+---+---+", "| a | b |", "+---+---+", "| 1 | 2 |", "+---+---+", "",
        ])

    def test_long_line_does_not_wait_for_newline(self):
        text = "x" * (HOLD_CHARS + 40)
        self.stream.feed(text)
        self.assertTrue(self.out, "超过 HOLD_CHARS 还没换行，应该先吐一截")
        self.assertLessEqual(len(self.out[0]), HOLD_CHARS)
        self.assertTrue(text.startswith(self.out[0]))

    def test_sanitize_before_display(self):
        stream = MarkdownStream(self.colors, self.out.append,
                                sanitize=lambda text: text.replace("<DSML>", ""))
        stream.feed("<DSML>hello\n")
        stream.finish()
        self.assertEqual(self.out, ["hello"])


if __name__ == "__main__":
    unittest.main()
