"""会话持久化（`wocc --last-session`）的离线测试。"""

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from woc import session
from woc.cli import multi

CFG = {
    "api_key": "test",
    "model": "deepseek-flash",
    "timeout": 30,
    "prefix": {"alias": "woc", "woc_prefix": "woc", "wocc_prefix": "wocc",
               "woc_chat_prefix": "woc_chat"},
    "loop": {"enabled": False, "max_steps": 5, "total_chars": 24000,
             "auto_safe": False},
    "sudo": {"capture_output": False},
    "context": {"ls_on_start": False},
    "exec": {"syntax_check": True},
    "ui": {"spinner": False},
    "session": {"persist": True, "max_messages": 40, "max_chars": 60000},
    "budget": {"context_tokens": 64000, "max_output_tokens": 4096,
               "max_input_chars": 32000},
}


class FakeTty:
    def isatty(self):
        return True


class SessionFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_roundtrip_only_user_assistant(self):
        messages = [
            {"role": "system", "content": "prefix"},
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "#CHAT: 回答"},
        ]
        self.assertTrue(session.save(messages))
        loaded = session.load()
        self.assertEqual([m["role"] for m in loaded["messages"]],
                         ["user", "assistant"])
        self.assertEqual(loaded["messages"][1]["content"], "#CHAT: 回答")

    def test_secrets_are_redacted(self):
        session.save([{"role": "user",
                       "content": "mysql -u root --password=hunter2"}])
        loaded = session.load()
        self.assertNotIn("hunter2", loaded["messages"][0]["content"])
        self.assertIn("***", loaded["messages"][0]["content"])

    def test_file_mode_is_0600(self):
        session.save([{"role": "user", "content": "x"}])
        mode = stat.S_IMODE(os.stat(session.session_path()).st_mode)
        self.assertEqual(mode, 0o600)

    def test_corrupt_file_returns_none(self):
        path = session.session_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(session.load())

    def test_wrong_version_returns_none(self):
        path = session.session_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 99, "messages": []}),
                        encoding="utf-8")
        self.assertIsNone(session.load())

    def test_trim_keeps_recent_and_starts_with_user(self):
        messages = []
        for index in range(10):
            messages.append({"role": "user", "content": "q%d" % index})
            messages.append({"role": "assistant", "content": "a%d" % index})
        kept = session.trim(messages, max_messages=4, max_chars=10000)
        self.assertEqual(len(kept), 4)
        self.assertEqual(kept[0]["content"], "q8")

    def test_trim_drops_leading_assistant(self):
        messages = [{"role": "assistant", "content": "a"},
                    {"role": "user", "content": "q"},
                    {"role": "assistant", "content": "b"}]
        kept = session.trim(messages, max_messages=2, max_chars=10000)
        self.assertEqual([m["role"] for m in kept], ["user", "assistant"])

    def test_long_message_is_truncated(self):
        session.save([{"role": "user", "content": "x" * 50000}])
        content = session.load()["messages"][0]["content"]
        self.assertLessEqual(len(content), session.MAX_MESSAGE_CHARS + 20)
        self.assertIn("已截断", content)

    def test_last_command_roundtrip(self):
        session.save([{"role": "user", "content": "q"}],
                     last={"command": "ls", "output": "a\n",
                           "stderr": "", "code": 0})
        loaded = session.load()
        self.assertEqual(loaded["last"]["command"], "ls")
        self.assertEqual(loaded["last"]["code"], 0)

    def test_last_command_is_redacted(self):
        session.save([{"role": "user", "content": "q"}],
                     last={"command": "mysql --password=hunter2",
                           "output": "", "stderr": "", "code": 1})
        self.assertNotIn("hunter2",
                         session.load()["last"]["command"])

    def test_context_note_mentions_staleness(self):
        note = session.context_note({"saved_at": "2026-10-19 10:00:00",
                                     "cwd": "/somewhere/else"})
        self.assertIn("2026-10-19 10:00:00", note)
        self.assertIn("过期", note)
        self.assertIn("session_context", note)


class MultiIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.stderr = io.StringIO()
        self.stdout = io.StringIO()

    def run_wocc(self, argv, inputs, cfg=None):
        with mock.patch.object(sys, "stdin", FakeTty()), \
                mock.patch.object(multi, "load_config", return_value=cfg or CFG), \
                mock.patch.object(multi, "interactive_input",
                                  side_effect=inputs), \
                redirect_stderr(self.stderr), redirect_stdout(self.stdout):
            multi.wocc_main(argv)

    def test_restores_last_session(self):
        session.save([{"role": "user", "content": "旧问题"},
                      {"role": "assistant", "content": "#CHAT: 旧回答"}],
                     last={"command": "du -sh .", "output": "1G\n",
                           "stderr": "", "code": 0})
        self.run_wocc(["--last-session"], [":q"])
        self.assertIn("已恢复上次会话", self.stderr.getvalue())
        self.assertEqual(multi.last_command(), "du -sh .")

    def test_missing_session_is_reported(self):
        self.run_wocc(["--last-session"], [":q"])
        self.assertIn("没有可恢复的会话", self.stderr.getvalue())

    def test_saves_after_each_turn(self):
        with mock.patch.object(multi, "run_turn", return_value=None):
            self.run_wocc([], ["你好", ":q"])
        loaded = session.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["messages"][0]["content"], "你好")

    def test_persist_disabled_writes_nothing(self):
        cfg = dict(CFG, session={"persist": False, "max_messages": 40,
                                 "max_chars": 60000})
        with mock.patch.object(multi, "run_turn", return_value=None):
            self.run_wocc([], ["你好", ":q"], cfg=cfg)
        self.assertFalse(session.session_path().exists())


if __name__ == "__main__":
    unittest.main()


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_save_creates_archive_and_lists_it(self):
        session.save([{"role": "user", "content": "q"}])
        items = session.list_sessions()
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["name"].startswith("session-"))
        self.assertEqual(items[0]["count"], 1)

    def test_load_by_name(self):
        session.save([{"role": "user", "content": "hello"}])
        name = session.list_sessions()[0]["name"]
        loaded = session.load(name=name)
        self.assertEqual(loaded["messages"][0]["content"], "hello")

    def test_load_accepts_json_suffix(self):
        session.save([{"role": "user", "content": "x"}])
        name = session.list_sessions()[0]["name"]
        self.assertIsNotNone(session.load(name=name + ".json"))

    def test_bad_names_are_rejected(self):
        for name in ("../etc/passwd", "a/b", "", "中文", "-x"):
            self.assertIsNone(session.load(name=name))
            self.assertIsNone(session._named_path(name))


class SessionFlagTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.stderr = io.StringIO()

    def run_cli(self, argv, inputs=(":q",)):
        with mock.patch.object(sys, "stdin", FakeTty()), \
                mock.patch.object(multi, "load_config", return_value=CFG), \
                mock.patch.object(multi, "interactive_input",
                                  side_effect=list(inputs)), \
                redirect_stderr(self.stderr), redirect_stdout(io.StringIO()):
            multi.wocc_main(argv)

    def test_sessions_lists_saved_ones(self):
        session.save([{"role": "user", "content": "q"}])
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli(["--sessions"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("已保存的会话", self.stderr.getvalue())

    def test_sessions_without_data(self):
        with self.assertRaises(SystemExit):
            self.run_cli(["--sessions"])
        self.assertIn("还没有保存过会话", self.stderr.getvalue())

    def test_resume_named_session(self):
        session.save([{"role": "user", "content": "旧问题"},
                      {"role": "assistant", "content": "#CHAT: 旧回答"}])
        name = session.list_sessions()[0]["name"]
        self.run_cli(["--resume", name])
        self.assertIn("已恢复上次会话", self.stderr.getvalue())

    def test_resume_without_name_exits_2(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli(["--resume"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("需要一个会话名", self.stderr.getvalue())
