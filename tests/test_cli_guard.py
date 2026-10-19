"""非终端输入的行为守卫。

背景：`read_key()` / `interactive_input()` 在非 tty 时会退化成 `input()`，
于是管道里的任意一行都会被当成“确认”，单轮 `woc` 的 `input()` 更是把返回值丢弃。
这等于让“每条命令都要人工确认”的前提在管道场景下失效。这里守住这条边界。
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from woc.cli import chat, multi, single

CFG = {
    "api_key": "",
    "prefix": {"alias": "woc", "woc_prefix": "woc", "wocc_prefix": "wocc",
               "woc_chat_prefix": "woc_chat"},
    "loop": {"enabled": False, "max_steps": 5, "total_chars": 24000,
             "auto_safe": False},
    "sudo": {"capture_output": False},
    "context": {"ls_on_start": False},
    "exec": {"syntax_check": True},
    "ui": {"spinner": False},
    "pipe": {"enabled": False, "max_chars": 32000},
}


class NonTtyGuardTest(unittest.TestCase):
    def run_cli(self, func, argv, module):
        fake_stdin = io.StringIO("some piped data\n")
        err = io.StringIO()
        with mock.patch.object(sys, "stdin", fake_stdin), \
                mock.patch.object(module, "load_config", return_value=CFG), \
                redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                func(argv)
        return ctx.exception.code, err.getvalue()

    def test_woc_refuses_when_pipe_mode_disabled(self):
        code, err = self.run_cli(single.woc_main, ["列出文件"], single)
        self.assertEqual(code, 2)
        self.assertIn("不是终端", err)

    def test_wocc_refuses_instead_of_auto_confirming(self):
        code, err = self.run_cli(multi.wocc_main, [], multi)
        self.assertEqual(code, 2)
        self.assertIn("需要终端", err)

    def test_chat_refuses_without_a_question(self):
        code, err = self.run_cli(chat.woc_chat_main, [], chat)
        self.assertEqual(code, 2)
        self.assertIn("需要终端", err)


if __name__ == "__main__":
    unittest.main()
