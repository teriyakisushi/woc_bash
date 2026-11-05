"""`--help` 文案的回归：三个入口各自的 flag 必须都出现。

文案集中在 `woc/cli/help.py`，这条测试防的是"新增 flag 忘了写进帮助"。
"""

import io
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout

from woc.cli import chat, help as help_mod, multi, single


def _capture(fn, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err), unittest.TestCase().assertRaises(SystemExit):
        fn(argv)
    return out.getvalue(), err.getvalue()


class SingleHelpTest(unittest.TestCase):
    def test_lists_every_option(self):
        out, _ = _capture(single.woc_main, ["--help"])
        for flag in ("-h, --help", "-c, --chat", "--completion", "--templates",
                     "--check-templates", "--install-templates", "--max-chars"):
            self.assertIn(flag, out)

    def test_no_argv_prints_same_help(self):
        # 有终端且没给参数时才打印用法；没有终端时走管道模式（stdin 当数据）
        with mock.patch.object(single.sys.stdin, "isatty", return_value=True):
            out, _ = _capture(single.woc_main, [])
        self.assertIn("--check-templates", out)


class MultiHelpTest(unittest.TestCase):
    def test_lists_every_option(self):
        out, err = _capture(multi.wocc_main, ["--help"])
        for flag in ("-h, --help", "--loop", "--capture-sudo", "--last-session",
                     "--sessions", "--resume"):
            self.assertIn(flag, out)
        # 会话内命令复用 multi.show_help，走 stderr
        for name in (":more", ":explain", ":search", ":help"):
            self.assertIn(name, err)


class ChatHelpTest(unittest.TestCase):
    def test_lists_every_option(self):
        out, _ = _capture(chat.woc_chat_main, ["--help"])
        for flag in ("-h, --help", "--search", "--no-search", ":search"):
            self.assertIn(flag, out)

    def test_via_woc_shows_woc_c_form(self):
        out, _ = _capture(lambda argv: chat.woc_chat_main(argv, name="woc", via_woc=True),
                          ["--help"])
        self.assertIn("woc -c", out)


class PrefixTest(unittest.TestCase):
    """命令名来自配置，帮助文案里不许出现硬编码的 woc。"""

    def test_uses_configured_names(self):
        cfg = {"prefix": {"woc_prefix": "qq", "wocc_prefix": "qqq",
                          "woc_chat_prefix": "qqc"}}
        out = io.StringIO()
        with redirect_stdout(out):
            help_mod.single_help("qq", cfg)
        text = out.getvalue()
        self.assertIn("qqq", text)
        self.assertIn("qqc", text)
        self.assertNotIn(" woc ", text)


if __name__ == "__main__":
    unittest.main()
