"""包门面必须是懒加载的。

`import woc.config` 不该拉起 colors（termios/tty）、llm、context —— compile.py
这类"只读配置"的工具依赖这一点，启动路径也依赖它。
"""

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class LazyImportTest(unittest.TestCase):
    def run_py(self, code):
        proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                              capture_output=True, text=True, timeout=30)
        return proc

    def test_import_config_does_not_load_colors(self):
        proc = self.run_py(
            "import sys, woc.config\nprint('woc.colors' in sys.modules)")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "False")

    def test_lazy_attribute_still_works(self):
        proc = self.run_py("from woc import load_config\n"
                           "print(callable(load_config))")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "True")

    def test_unknown_attribute_raises(self):
        proc = self.run_py("import woc\n"
                           "try:\n"
                           "    woc.not_a_real_name\n"
                           "except AttributeError:\n"
                           "    print('ok')\n")
        self.assertEqual(proc.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
