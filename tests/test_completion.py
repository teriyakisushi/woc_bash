"""shell 补全脚本生成的离线测试。"""

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from woc.cli.completion import render

NAMES = ["woc", "wocc", "woc_chat"]


class RenderTest(unittest.TestCase):
    def test_bash_script(self):
        script = render("bash", NAMES)
        self.assertIn("complete -F _woc_complete woc wocc woc_chat", script)
        self.assertIn("--sessions", script)
        self.assertIn("wocc)", script)

    def test_zsh_script(self):
        script = render("zsh", NAMES)
        self.assertTrue(script.startswith("#compdef woc wocc woc_chat"))
        self.assertIn("compdef _woc_complete", script)

    def test_unknown_shell(self):
        with self.assertRaises(ValueError):
            render("fish", NAMES)


class CompletionCliTest(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "woc.py", *args], cwd=ROOT,
                              capture_output=True, text=True, timeout=30)

    def test_bash_exit_zero(self):
        proc = self.run_cli("--completion", "bash")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("complete -F", proc.stdout)

    def test_missing_shell_exits_2(self):
        proc = self.run_cli("--completion")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("用法", proc.stderr)


if __name__ == "__main__":
    unittest.main()
