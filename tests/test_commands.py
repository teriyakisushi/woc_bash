"""会话内命令 `:steps` / `:explain` 的离线测试。"""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from woc import loop as L

CFG = {"api_key": "test", "prefix": {"alias": "woc"}, "ui": {"spinner": False}}


class FakeLLM:
    def __init__(self, reply="#CHAT: 解释完成"):
        self.reply = reply
        self.messages = []
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()

    def __enter__(self):
        self._saved = L.call_llm_messages
        L.call_llm_messages = self.llm
        self._out = redirect_stdout(self.stdout)
        self._err = redirect_stderr(self.stderr)
        self._out.__enter__()
        self._err.__enter__()
        return self

    def __exit__(self, *exc):
        self._err.__exit__(*exc)
        self._out.__exit__(*exc)
        L.call_llm_messages = self._saved
        return False

    def llm(self, messages, cfg, on_chunk=None, on_mode=None):
        self.messages.append(messages)
        if on_chunk:
            on_chunk("#CHAT: 解释完成")
        return self.reply


class StepsTest(unittest.TestCase):
    def setUp(self):
        L._STEPS.clear()

    def tearDown(self):
        L._STEPS.clear()

    def test_empty(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertFalse(L.show_steps("woc"))
        self.assertIn("还没有执行过", err.getvalue())

    def test_lists_all_statuses(self):
        L._STEPS.extend([
            {"step": 1, "cmd": "ls", "status": "run", "code": 0, "ms": 120},
            {"step": 2, "cmd": "cat x", "status": "skip"},
            {"step": 3, "cmd": "rm -rf /", "status": "deny"},
            {"step": 4, "cmd": "echo (", "status": "syntax"},
        ])
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertTrue(L.show_steps("woc"))
        text = err.getvalue()
        self.assertIn("本回合 4 步", text)
        self.assertIn("1. [0  0.12s] ls", text)
        self.assertIn("2. [跳过] cat x", text)
        self.assertIn("3. [拒绝] rm -rf /", text)
        self.assertIn("4. [语法错误] echo (", text)


class ExplainTest(unittest.TestCase):
    def setUp(self):
        self._saved = dict(L._LAST)
        L._LAST.update(command="", output="", stderr="", code=None)

    def tearDown(self):
        L._LAST.update(self._saved)

    def test_without_command(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertFalse(L.explain_last("woc", CFG))
        self.assertIn("还没有执行过", err.getvalue())

    def test_sends_command_output_and_exit_code(self):
        L._LAST.update(command="du -sh /var", output="1.2G /var\n",
                       stderr="", code=0)
        with FakeLLM() as fake:
            self.assertTrue(L.explain_last("woc", CFG))
        user = fake.messages[0][-1]["content"]
        self.assertIn("du -sh /var", user)
        self.assertIn("1.2G /var", user)
        self.assertIn("<exit_code>0</exit_code>", user)
        self.assertIn("解释完成", fake.stdout.getvalue())

    def test_includes_stderr(self):
        L._LAST.update(command="false", output="", stderr="boom", code=1)
        with FakeLLM() as fake:
            self.assertTrue(L.explain_last("woc", CFG))
        user = fake.messages[0][-1]["content"]
        self.assertIn("boom", user)
        self.assertIn("<exit_code>1</exit_code>", user)


if __name__ == "__main__":
    unittest.main()


class HelpTest(unittest.TestCase):
    def test_lists_all_commands(self):
        from woc.cli import multi
        err = io.StringIO()
        with redirect_stderr(err):
            multi.show_help("woc")
        text = err.getvalue()
        for name in (":more", ":last", ":steps", ":explain", ":again", ":help"):
            self.assertIn(name, text)


class AgainTest(unittest.TestCase):
    def setUp(self):
        self._saved = dict(L._LAST)
        L._LAST.update(command="", output="", stderr="", code=None)

    def tearDown(self):
        L._LAST.update(self._saved)

    def test_without_command(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertFalse(L.rerun_last([], "woc", CFG))
        self.assertIn("还没有执行过", err.getvalue())

    def test_denied_command_is_not_executed(self):
        L._LAST.update(command="rm -rf /", output="", stderr="", code=0)
        with mock.patch.object(L, "_execute") as execute:
            with redirect_stderr(io.StringIO()):
                self.assertFalse(L.rerun_last([], "woc", CFG))
        execute.assert_not_called()

    def test_reruns_and_appends_history(self):
        L._LAST.update(command="ls -l", output="", stderr="", code=0)
        messages = []
        with mock.patch.object(L, "_execute",
                               return_value=("ok", "out", "", 0, False)), \
                mock.patch.object(L, "_confirm", return_value="run"), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertTrue(L.rerun_last(messages, "woc", CFG))
        self.assertEqual(messages[0],
                         {"role": "assistant", "content": "ls -l"})
        self.assertEqual(messages[1], {"role": "user", "content": "ok"})
        self.assertEqual(L._LAST["code"], 0)


class FoldTest(unittest.TestCase):
    def output(self, limit):
        buf = io.StringIO()
        text = "".join("line%d\n" % i for i in range(30))
        with redirect_stdout(buf):
            L._print_output(text, "", 0, "woc", limit=limit)
        return buf.getvalue()

    def test_limit_triggers_folding(self):
        self.assertIn("已折叠", self.output(5))

    def test_zero_disables_folding(self):
        self.assertNotIn("已折叠", self.output(0))
