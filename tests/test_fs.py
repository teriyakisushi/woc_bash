"""`woc-fs` 的离线测试：read 的行窗口、write 的原子落盘、edit 的唯一匹配。

这三条是模型最容易踩的坑：读到半截、写到一半、改错地方。所以每个失败分支
（0 匹配 / 多处匹配 / 二进制 / 目录不存在）都要有自己的用例。
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from woc import fs


class FsCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)

    def run_fs(self, *argv, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        saved = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fs.main(list(argv))
        finally:
            sys.stdin = saved
        return code, out.getvalue(), err.getvalue()

    def make(self, name, text, raw=False):
        path = self.root / name
        if raw:
            path.write_bytes(text)
        else:
            path.write_bytes(text.encode("utf-8"))
        return path


class ReadTest(FsCase):
    def test_window_has_header_and_numbers(self):
        path = self.make("a.txt", "".join(f"l{i}\n" for i in range(1, 11)))
        code, out, err = self.run_fs("read", str(path), "--lines", "3:5")
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertEqual(lines[0], f"{path}: 3-5 of 10 lines")
        self.assertEqual(lines[1:], ["   3| l3", "   4| l4", "   5| l5"])

    def test_head_default_and_explicit(self):
        path = self.make("a.txt", "".join(f"l{i}\n" for i in range(1, 101)))
        _code, out, _err = self.run_fs("read", str(path), "--head", "2")
        self.assertIn("1-2 of 100 lines", out)
        self.assertEqual(len(out.splitlines()), 3)

    def test_tail(self):
        path = self.make("a.txt", "".join(f"l{i}\n" for i in range(1, 11)))
        _code, out, _err = self.run_fs("read", str(path), "--tail", "2")
        self.assertIn("9-10 of 10 lines", out)
        self.assertTrue(out.rstrip().endswith("10| l10"))

    def test_open_ended_range_runs_to_eof(self):
        path = self.make("a.txt", "".join(f"l{i}\n" for i in range(1, 11)))
        _code, out, _err = self.run_fs("read", str(path), "--lines", "9:")
        self.assertIn("9-10 of 10 lines", out)

    def test_raw_skips_header(self):
        path = self.make("a.txt", "alpha\nbeta\n")
        code, out, err = self.run_fs("read", str(path), "--raw")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "alpha\nbeta\n")

    def test_missing_file_is_one_line_error(self):
        code, out, err = self.run_fs("read", str(self.root / "nope.txt"))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(len(err.splitlines()), 1)
        self.assertIn("No such file", err)

    def test_binary_is_refused(self):
        path = self.make("blob.bin", b"PK\x03\x04\x00\x00binary", raw=True)
        code, _out, err = self.run_fs("read", str(path))
        self.assertEqual(code, 1)
        self.assertIn("binary", err)

    def test_max_chars_truncates_with_a_note(self):
        path = self.make("a.txt", "x" * 500 + "\n")
        code, out, err = self.run_fs("read", str(path), "--max-chars", "100")
        self.assertEqual(code, 0, err)
        self.assertLess(len(out), 200)
        self.assertIn("截断于 100 字符", out)


class WriteTest(FsCase):
    def test_content_flag_writes_file(self):
        path = self.root / "new.txt"
        code, out, err = self.run_fs("write", str(path), "--content", "hello\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(path.read_text(encoding="utf-8"), "hello\n")
        self.assertIn("wrote 6 chars, 1 lines", out)

    def test_stdin_is_the_default_source(self):
        path = self.root / "new.txt"
        code, _out, err = self.run_fs("write", str(path), stdin="from stdin\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(path.read_text(encoding="utf-8"), "from stdin\n")

    def test_content_supports_at_file(self):
        body = self.root / "body.txt"
        body.write_bytes(b"import os\n")
        path = self.root / "copy.py"
        code, _out, err = self.run_fs("write", str(path), "--content", f"@{body}")
        self.assertEqual(code, 0, err)
        self.assertEqual(path.read_text(encoding="utf-8"), "import os\n")

    def test_dry_run_touches_nothing(self):
        path = self.root / "ghost.txt"
        code, out, _err = self.run_fs("write", str(path), "--content", "x\n", "--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse(path.exists())
        self.assertIn("would write", out)

    def test_missing_directory_needs_mkdir(self):
        path = self.root / "deep" / "dir" / "f.txt"
        code, _out, err = self.run_fs("write", str(path), "--content", "x\n")
        self.assertEqual(code, 1)
        self.assertIn("--mkdir", err)
        code, _out, err = self.run_fs("write", str(path), "--content", "x\n", "--mkdir")
        self.assertEqual(code, 0, err)
        self.assertEqual(path.read_text(encoding="utf-8"), "x\n")

    def test_existing_mode_is_preserved(self):
        path = self.make("run.sh", "#!/bin/sh\n")
        os.chmod(path, 0o755)
        code, _out, err = self.run_fs("write", str(path), "--content", "#!/bin/sh\necho hi\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(path.stat().st_mode & 0o777, 0o755)

    def test_backup_keeps_previous_bytes(self):
        path = self.make("a.txt", "old\n")
        code, _out, err = self.run_fs("write", str(path), "--content", "new\n", "--backup")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.root / "a.txt.bak").read_text(encoding="utf-8"), "old\n")

    def test_append(self):
        path = self.make("a.txt", "one\n")
        code, _out, err = self.run_fs("write", str(path), "--content", "two\n", "--append")
        self.assertEqual(code, 0, err)
        self.assertEqual(path.read_text(encoding="utf-8"), "one\ntwo\n")


class EditTest(FsCase):
    def test_unique_replacement(self):
        path = self.make("a.py", "a = 1\nb = 2\nc = 3\n")
        code, out, err = self.run_fs("edit", str(path), "--old", "b = 2", "--new", "b = 20")
        self.assertEqual(code, 0, err)
        self.assertEqual(path.read_text(encoding="utf-8"), "a = 1\nb = 20\nc = 3\n")
        self.assertIn("replaced 1 place(s)", out)

    def test_crlf_is_preserved(self):
        path = self.make("a.txt", b"one\r\ntwo\r\nthree\r\n", raw=True)
        code, _out, err = self.run_fs("edit", str(path), "--old", "two", "--new", "2")
        self.assertEqual(code, 0, err)
        self.assertEqual(path.read_bytes(), b"one\r\n2\r\nthree\r\n")

    def test_zero_match_reports_closest_line(self):
        path = self.make("a.py", "def alpha():\n    pass\n")
        code, out, err = self.run_fs("edit", str(path), "--old", "def alpah():", "--new", "x")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("没找到", err)
        self.assertIn("第 1 行", err)
        self.assertEqual(path.read_text(encoding="utf-8"), "def alpha():\n    pass\n")

    def test_ambiguous_match_refuses_and_leaves_file_alone(self):
        path = self.make("a.txt", "x\nx\nx\n")
        code, _out, err = self.run_fs("edit", str(path), "--old", "x", "--new", "y")
        self.assertEqual(code, 1)
        self.assertIn("命中 3 处", err)
        self.assertIn("--all", err)
        self.assertEqual(path.read_text(encoding="utf-8"), "x\nx\nx\n")

    def test_ambiguous_with_all(self):
        path = self.make("a.txt", "x\nx\nx\n")
        code, out, err = self.run_fs("edit", str(path), "--old", "x", "--new", "y",
                                     "--all")
        self.assertEqual(code, 0, err)
        self.assertEqual(path.read_text(encoding="utf-8"), "y\ny\ny\n")
        self.assertIn("replaced 3 place(s)", out)

    def test_multiline_old_from_file(self):
        body = self.root / "old.txt"
        body.write_bytes(b"if a:\n    b\n")
        path = self.make("a.py", "if a:\n    b\n    c\n")
        code, out, err = self.run_fs("edit", str(path), "--old", f"@{body}",
                                     "--new", "if a:\n    b = 1\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(path.read_text(encoding="utf-8"), "if a:\n    b = 1\n    c\n")
        self.assertIn("3 -> 3 lines", out)

    def test_new_omitted_deletes(self):
        path = self.make("a.txt", "keep drop\n")
        code, _out, err = self.run_fs("edit", str(path), "--old", " drop")
        self.assertEqual(code, 0, err)
        self.assertEqual(path.read_text(encoding="utf-8"), "keep\n")

    def test_dry_run_reports_without_writing(self):
        path = self.make("a.txt", "x\n")
        code, out, _err = self.run_fs("edit", str(path), "--old", "x", "--new", "y",
                                      "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would replace", out)
        self.assertEqual(path.read_text(encoding="utf-8"), "x\n")

    def test_empty_old_is_an_error(self):
        path = self.make("a.txt", "x\n")
        code, _out, err = self.run_fs("edit", str(path), "--old", "")
        self.assertEqual(code, 1)
        self.assertIn("--old 是空的", err)


class InstallerNameTest(unittest.TestCase):
    """`woc-fs` 的名字跟着 woc_prefix 走，不额外占一个 [prefix] 配置键。"""

    def test_command_names_include_fs(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "woc_compile_under_test", os.path.join(ROOT, "compile.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        names = {"woc_prefix": "wc", "wocc_prefix": "wcc", "woc_chat_prefix": "wcchat"}
        pairs = module.command_names(names)
        self.assertEqual(pairs[-1], ("wc-fs", "wocfs.py"))
        self.assertEqual(len(pairs), 4)


if __name__ == "__main__":
    unittest.main()
