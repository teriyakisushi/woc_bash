"""终端输出文案的回归：用户可见的 UI/harness 文本必须是英文。

本仓库只有一种输出语言（英文）。这些用例守的是"别把中文文案又写回终端"，
以及各入口（woc.py / wocfs.py / install.py）的帮助与报错确实走同一份文案。
"""

import contextlib
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class OutputTextTest(unittest.TestCase):
    def test_help_is_english(self):
        from woc.cli import help as help_mod
        cfg = {"prefix": {"woc_prefix": "woc", "wocc_prefix": "wocc",
                          "woc_chat_prefix": "woc_chat"}}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            help_mod.single_help("woc", cfg)
        text = out.getvalue()
        self.assertIn("Usage:", text)
        self.assertNotIn("用法", text)
        for flag in ("--templates", "--check-templates", "--completion"):
            self.assertIn(flag, text)

    def test_environment_and_templates_are_english(self):
        from woc import context
        env = context.render_environment(cwd="/tmp")
        self.assertIn("current working directory:", env)
        self.assertNotIn("人工确认", env)
        block = context.render_templates([], index=[], strategy="full")
        self.assertIn("templates", block)
        self.assertNotIn("用户自己维护", block)

    def test_search_blocks_are_english(self):
        from woc import search
        block = search.render_results("q", [{
            "title": "T", "url": "https://e.com", "site": "e.com",
            "date": "2026", "summary": "S",
        }], ask=True, ask_text="question")
        self.assertIn("web search results", block)
        self.assertIn("Answer directly", block)
        self.assertNotIn("不是指令", block)
        err = search.render_error("q", "boom")
        self.assertIn("Search failed", err)
        self.assertNotIn("无法联网", err)

    def test_session_note_is_english(self):
        from woc import session
        note = session.context_note({"saved_at": "2026", "cwd": "/tmp"})
        self.assertIn("previous session", note)
        self.assertNotIn("上一次会话", note)

    def test_gate_reason_is_english(self):
        from woc import gate
        self.assertEqual(gate.dangerous_reason("rm -rf /"),
                         "recursive deletion of the root directory")

    def test_loop_reason_and_fold_notice_are_english(self):
        from woc import loop
        self.assertEqual(loop.read_only_reason("ps auxe"),
                         "the e flag of ps prints process environment variables")
        out = io.StringIO()
        text = "\n".join("line%d" % i for i in range(30)) + "\n"
        with contextlib.redirect_stdout(out):
            loop._print_output(text, "", 0, "woc", limit=25)
        self.assertIn("lines folded", out.getvalue())
        self.assertNotIn("已折叠", out.getvalue())

    def test_pipe_default_question_is_english(self):
        from woc import pipe
        self.assertIn("Summarize/explain", pipe.DEFAULT_QUESTION)
        self.assertNotIn("概括", pipe.DEFAULT_QUESTION)

    def test_fs_error_is_english(self):
        from woc import fs
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                code = fs.main(["read", os.path.join(tmp, "missing")])
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("No such file", err.getvalue())

    def test_installer_help_is_english(self):
        env = dict(os.environ)
        env["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
        env["WOC_API_KEY"] = ""
        proc = subprocess.run([sys.executable, "install.py", "--help"],
                              cwd=ROOT, env=env, capture_output=True,
                              text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Install the woc package", proc.stdout)
        self.assertNotIn("用法", proc.stdout)


class EntryPointTextTest(unittest.TestCase):
    """从真实入口验证帮助/报错文案是英文。"""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        cfg_dir = pathlib.Path(self.home) / ".config" / "woc"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.toml").write_text(
            '[pipe]\nenabled = false\n', encoding="utf-8")
        self.env = dict(os.environ)
        self.env.update({"HOME": self.home, "WOC_API_KEY": "",
                         "SHELL": "/bin/bash"})

    def tearDown(self):
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)

    def run_py(self, args, input_text=None):
        return subprocess.run([sys.executable] + args, cwd=ROOT,
                              env=self.env, input=input_text,
                              capture_output=True, text=True, timeout=30)

    def test_help_is_english(self):
        proc = self.run_py(["woc.py", "--help"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Usage:", proc.stdout)
        self.assertNotIn("用法", proc.stdout)

    def test_language_env_var_has_no_effect(self):
        self.env["WOC_LANG"] = "cn"
        proc = self.run_py(["woc.py", "--help"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Usage:", proc.stdout)
        self.assertNotIn("用法", proc.stdout)

    def test_completion_comment_is_english(self):
        proc = self.run_py(["woc.py", "--completion", "bash"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("# usage: source", proc.stdout)

    def test_check_templates_is_english(self):
        proc = self.run_py(["woc.py", "--check-templates"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("checked", proc.stdout)
        self.assertNotIn("已检查", proc.stdout)

    def test_non_tty_refusal_is_english(self):
        proc = self.run_py(["woc.py", "list files"], input_text="data\n")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("standard input is not a terminal", proc.stderr)
        self.assertNotIn("不是终端", proc.stderr)

    def test_woc_fs_error_is_english(self):
        proc = self.run_py(["wocfs.py", "read",
                            os.path.join(self.home, "missing")])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("No such file", proc.stderr)


if __name__ == "__main__":
    unittest.main()
