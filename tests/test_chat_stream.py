"""`woc -c` 必须边收边显示。

背景：`call_llm_messages_stream()` 一直是 SSE，但 chat 曾用
`on_chunk=chunks.append` 把增量攒进列表，等整条回答收完才一次性渲染；
用户在终端里看到的就是"没有流式"。这里守住"流没结束屏幕上就得有内容"。
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from woc.cli import chat

CFG = {
    "api_key": "test",
    "prefix": {"alias": "woc", "woc_prefix": "woc", "wocc_prefix": "wocc",
               "woc_chat_prefix": "woc_chat"},
    "ui": {"spinner": False},
}


class ChatStreamTest(unittest.TestCase):
    def run_chat(self, pieces):
        """跑一轮 one-shot 聊天，返回（最终 stdout、每轮中途的 stdout、messages）。"""
        transcript = io.StringIO()
        midway = []
        seen_messages = []

        def fake_stream(cfg, messages, on_chunk=None, on_mode=None):
            seen_messages.append(messages)
            for piece in pieces:
                on_chunk(piece)
                midway.append(transcript.getvalue())   # 流还没结束时的屏幕内容
            return "".join(pieces)

        with mock.patch.object(sys, "stdin", io.StringIO("")), \
                mock.patch.object(chat, "load_config", return_value=CFG), \
                mock.patch.object(chat, "call_llm_messages_stream", fake_stream), \
                redirect_stdout(transcript), redirect_stderr(io.StringIO()):
            chat.woc_chat_main(["讲个笑话"])
        return transcript.getvalue(), midway, seen_messages

    def test_prints_while_streaming(self):
        final, midway, _messages = self.run_chat(["第一行", "\n", "第二行\n"])
        self.assertIn("第一行", midway[1], "增量到了就该上屏，不能等流结束")
        self.assertIn("第二行", midway[2])
        self.assertIn("[assistant] ", final)
        self.assertIn("第一行\n第二行\n", final)

    def test_history_keeps_full_answer(self):
        _final, _midway, messages = self.run_chat(["你好\n"])
        self.assertEqual(messages[0][-1], {"role": "assistant", "content": "你好\n"})

    def test_control_marker_never_shown(self):
        final, _midway, _messages = self.run_chat(
            ["<｜｜DSML｜｜>", "正文\n"])
        self.assertNotIn("DSML", final)
        self.assertIn("正文", final)


if __name__ == "__main__":
    unittest.main()
