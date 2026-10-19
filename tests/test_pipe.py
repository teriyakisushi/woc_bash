"""管道模式（stdin 当数据）的离线测试。

守三件事：
1. 输入处理：解码、ANSI 剥离、二进制拒绝、头尾截断；
2. 消息形状：数据只在 user 消息里，system 前缀不含模板；
3. 行为边界：只回答、不执行，任何失败都有明确退出码。
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

from woc import pipe as P
from woc.cli import single
from woc.llm import LLMError, StreamFilter, StreamMode

CFG = {
    "api_key": "test",
    "prefix": {"alias": "woc", "woc_prefix": "woc"},
    "pipe": {"enabled": True, "max_chars": 32000},
    "ui": {"spinner": False},
}


class FakeStdin:
    """sys.stdin 的替身：有 .buffer（二进制）也有 read()（文本兜底）。"""

    def __init__(self, raw=b"", tty=False):
        self.buffer = io.BytesIO(raw)
        self._text = io.StringIO(raw.decode("utf-8", "replace"))
        self._tty = tty

    def isatty(self):
        return self._tty

    def read(self, size=-1):
        return self._text.read(size)


class PipeHarness:
    """替换 LLM 与 stdin，收集 stdout/stderr。"""

    def __init__(self, reply="#CHAT: 分析完成", raw=b"hello\n",
                 error=None):
        self.reply = reply
        self.raw = raw
        self.reply_mode = None
        self.error = error
        self.messages = []
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()

    def __enter__(self):
        self._saved_llm = P.call_llm_messages
        self._saved_stdin = sys.stdin
        P.call_llm_messages = self.llm
        sys.stdin = FakeStdin(self.raw)
        self._out = redirect_stdout(self.stdout)
        self._err = redirect_stderr(self.stderr)
        self._out.__enter__()
        self._err.__enter__()
        return self

    def __exit__(self, *exc):
        self._err.__exit__(*exc)
        self._out.__exit__(*exc)
        P.call_llm_messages = self._saved_llm
        sys.stdin = self._saved_stdin
        return False

    def llm(self, messages, cfg, on_chunk=None, on_mode=None):
        self.messages.append(messages)
        if self.error:
            raise self.error
        # 走真实的 StreamFilter：标记剥离与 COMMAND/CHAT 判定都和线上一致
        flt = StreamFilter()
        emitted = flt.feed(self.reply) + flt.finish()
        if on_chunk:
            on_chunk(emitted)
        if on_mode:
            on_mode(flt.mode)
        return flt.acc.strip()


class ReadStdinTest(unittest.TestCase):
    def test_decodes_and_strips_trailing_newlines(self):
        with mock.patch.object(sys, "stdin", FakeStdin(b"a\nb\n\n")):
            text, truncated = P.read_stdin(1000)
        self.assertEqual(text, "a\nb")
        self.assertFalse(truncated)

    def test_strips_ansi_escapes(self):
        with mock.patch.object(sys, "stdin",
                               FakeStdin(b"\x1b[31merror\x1b[0m")):
            text, _ = P.read_stdin(1000)
        self.assertEqual(text, "error")

    def test_rejects_binary(self):
        with mock.patch.object(sys, "stdin", FakeStdin(b"\x00\x01\x02")):
            with self.assertRaises(P.PipeInputError):
                P.read_stdin(1000)

    def test_crlf_is_normalized(self):
        with mock.patch.object(sys, "stdin", FakeStdin(b"a\r\nb\rc")):
            text, _ = P.read_stdin(1000)
        self.assertEqual(text, "a\nb\nc")

    def test_truncates_and_marks(self):
        payload = "x" * 500
        with mock.patch.object(sys, "stdin", FakeStdin(payload.encode())):
            text, truncated = P.read_stdin(100)
        self.assertTrue(truncated)
        self.assertIn("已截断", text)

    def test_multibyte_not_split_into_replacement_chars(self):
        payload = "日志分析" * 50
        with mock.patch.object(sys, "stdin",
                               FakeStdin(payload.encode("utf-8"))):
            text, truncated = P.read_stdin(60)
        self.assertTrue(truncated)
        self.assertNotIn("\ufffd", text)


class TruncateMiddleTest(unittest.TestCase):
    def test_keeps_head_and_tail(self):
        text = "HEAD" + "a" * 200 + "TAIL"
        cut, removed = P.truncate_middle(text, 60)
        self.assertTrue(cut.startswith("HEAD"))
        self.assertTrue(cut.endswith("TAIL"))
        self.assertGreater(removed, 0)
        self.assertIn("已截断", cut)

    def test_noop_when_short(self):
        text, removed = P.truncate_middle("short", 100)
        self.assertEqual(text, "short")
        self.assertEqual(removed, 0)


class BrokenPipeTest(unittest.TestCase):
    def test_broken_pipe_exits_141_without_traceback(self):
        class BrokenStdout:
            def write(self, text):
                raise BrokenPipeError

            def flush(self):
                pass

        codes = []
        with mock.patch.object(sys, "stdout", BrokenStdout()), mock.patch.object(
                P.os, "_exit", side_effect=codes.append):
            P._write("x")
        self.assertEqual(codes, [141])


class BuildMessagesTest(unittest.TestCase):
    def test_shape(self):
        messages = P.build_pipe_messages("分析一下", "line1\nline2")
        self.assertEqual([m["role"] for m in messages],
                         ["system", "system", "user"])
        self.assertIn("DATA", messages[0]["content"])
        joined = "".join(m["content"] for m in messages)
        self.assertNotIn("<templates>", joined)
        self.assertIn("<stdin>", messages[2]["content"])
        self.assertIn("line1\nline2", messages[2]["content"])
        self.assertIn("分析一下", messages[2]["content"])

    def test_truncated_attribute(self):
        messages = P.build_pipe_messages("q", "data", truncated=True)
        self.assertIn('truncated="true"', messages[2]["content"])

    def test_no_confirm_claim_in_environment(self):
        messages = P.build_pipe_messages("q", "data")
        self.assertNotIn("人工确认", messages[1]["content"])


class RunPipeModeTest(unittest.TestCase):
    def test_happy_path_prints_answer_only(self):
        with PipeHarness(reply="#CHAT: 日志里有一次 OOM") as harness:
            code = P.run_pipe_mode(["分析"], CFG)
        self.assertEqual(code, 0)
        self.assertEqual(harness.stdout.getvalue().strip(),
                         "日志里有一次 OOM")
        self.assertIn("hello", harness.messages[0][2]["content"])

    def test_streaming_keeps_stdout_clean(self):
        with PipeHarness(reply="#CHAT: 第一段") as harness:
            self.assertEqual(P.run_pipe_mode(["q"], CFG), 0)
        self.assertEqual(harness.stdout.getvalue(), "第一段\n")

    def test_default_question_when_no_argv(self):
        with PipeHarness() as harness:
            self.assertEqual(P.run_pipe_mode([], CFG), 0)
        self.assertIn(P.DEFAULT_QUESTION, harness.messages[0][2]["content"])

    def test_empty_stdin_exits_2(self):
        with PipeHarness(raw=b"") as harness:
            self.assertEqual(P.run_pipe_mode(["q"], CFG), 2)
        self.assertIn("标准输入为空", harness.stderr.getvalue())

    def test_binary_exits_2(self):
        with PipeHarness(raw=b"\x00binary") as harness:
            self.assertEqual(P.run_pipe_mode(["q"], CFG), 2)
        self.assertIn("二进制", harness.stderr.getvalue())

    def test_llm_error_exits_3(self):
        with PipeHarness(error=LLMError("boom")) as harness:
            self.assertEqual(P.run_pipe_mode(["q"], CFG), 3)
        self.assertIn("boom", harness.stderr.getvalue())

    def test_command_reply_is_never_executed(self):
        with PipeHarness(reply="rm -rf /tmp/x") as harness:
            code = P.run_pipe_mode(["q"], CFG)
        self.assertEqual(code, 0)
        self.assertIn("rm -rf /tmp/x", harness.stdout.getvalue())
        self.assertEqual(harness.stderr.getvalue(), "")

    def test_config_max_chars_is_used(self):
        cfg = dict(CFG, pipe={"enabled": True, "max_chars": 20})
        with PipeHarness(raw=("y" * 500).encode()) as harness:
            self.assertEqual(P.run_pipe_mode(["q"], cfg), 0)
        self.assertIn("已截断", harness.messages[0][2]["content"])
        self.assertIn("已截断", harness.stderr.getvalue())


class SingleDispatchTest(unittest.TestCase):
    def test_non_tty_enters_pipe_mode(self):
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", FakeStdin(b"piped output")), \
                mock.patch.object(single, "load_config", return_value=CFG), \
                mock.patch.object(P, "call_llm_messages",
                                  return_value="#CHAT: 看懂了"), \
                redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                single.woc_main(["这是什么"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("看懂了", out.getvalue())


class ConfigTest(unittest.TestCase):
    def test_defaults_and_clamp(self):
        from woc.config import pipe_enabled, pipe_max_chars
        self.assertTrue(pipe_enabled({}))
        self.assertEqual(pipe_max_chars({}), 32000)
        # 显式值也要被预算夹住：默认 max_input_chars 就是 32000
        self.assertEqual(
            pipe_max_chars({"pipe": {"max_chars": 10 ** 9}}), 32000)
        self.assertEqual(
            pipe_max_chars({"pipe": {"max_chars": "bad"}}), 32000)

    def test_env_override(self):
        from woc.config import load_config
        with mock.patch.dict(os.environ, {"WOC_PIPE": "0",
                                          "WOC_PIPE_MAX_CHARS": "500"}):
            cfg = load_config(path="/nonexistent/woc.toml")
        self.assertFalse(cfg["pipe"]["enabled"])
        self.assertEqual(cfg["pipe"]["max_chars"], 500)


if __name__ == "__main__":
    unittest.main()


class SplitMaxCharsTest(unittest.TestCase):
    def test_separate_form(self):
        args, override = P.split_max_chars(["--max-chars", "500", "看看"])
        self.assertEqual(args, ["看看"])
        self.assertEqual(override, 500)

    def test_equals_form(self):
        args, override = P.split_max_chars(["--max-chars=500", "看看"])
        self.assertEqual(args, ["看看"])
        self.assertEqual(override, 500)

    def test_absent(self):
        args, override = P.split_max_chars(["看看", "日志"])
        self.assertEqual(args, ["看看", "日志"])
        self.assertIsNone(override)

    def test_invalid_values_raise(self):
        for argv in (["--max-chars", "abc"], ["--max-chars", "0"],
                     ["--max-chars=,", "--max-chars"], ["--max-chars"]):
            with self.assertRaises(ValueError):
                P.split_max_chars(argv)


class DetectFormatTest(unittest.TestCase):
    def test_git_diff(self):
        text = ("diff --git a/x.py b/x.py\nindex 111..222 100644\n"
                "--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,3 @@\n-a\n+b\n")
        self.assertEqual(P.detect_format(text), "diff")

    def test_hunk_header_only(self):
        self.assertEqual(P.detect_format("@@ -1,2 +3,4 @@\n-a\n+b\n"), "diff")

    def test_json_object(self):
        self.assertEqual(P.detect_format('{"a": 1}'), "json")

    def test_json_array(self):
        self.assertEqual(P.detect_format("[1, 2, 3]"), "json")

    def test_plain_log_is_not_guessed(self):
        self.assertEqual(P.detect_format("2026-10-19 09:00:01 INFO hi\n"), "")

    def test_broken_json_falls_back_to_unknown(self):
        self.assertEqual(P.detect_format('{"a": '), "")


class FormatAttributeTest(unittest.TestCase):
    def test_format_goes_into_block(self):
        messages = P.build_pipe_messages("q", "{}", fmt="json")
        self.assertIn('<stdin format="json">', messages[2]["content"])

    def test_no_format_no_attribute(self):
        messages = P.build_pipe_messages("q", "text")
        self.assertEqual(messages[2]["content"].splitlines()[0], "<stdin>")

    def test_truncated_and_format_together(self):
        messages = P.build_pipe_messages("q", "x", truncated=True, fmt="diff")
        head = messages[2]["content"].splitlines()[0]
        self.assertIn('truncated="true"', head)
        self.assertIn('format="diff"', head)


class MaxCharsOverrideTest(unittest.TestCase):
    def test_override_truncates(self):
        with PipeHarness(raw=("y" * 500).encode()) as harness:
            code = P.run_pipe_mode(["--max-chars", "20", "q"], CFG)
        self.assertEqual(code, 0)
        self.assertIn("输入超过 20 字符", harness.stderr.getvalue())
        self.assertIn("已截断", harness.messages[0][2]["content"])

    def test_override_equals_form(self):
        with PipeHarness(raw=("y" * 500).encode()) as harness:
            self.assertEqual(P.run_pipe_mode(["--max-chars=30", "q"], CFG), 0)
        self.assertIn("输入超过 30 字符", harness.stderr.getvalue())

    def test_invalid_override_exits_2(self):
        with PipeHarness(raw=b"data") as harness:
            self.assertEqual(P.run_pipe_mode(["--max-chars", "abc", "q"], CFG), 2)
        self.assertIn("正整数", harness.stderr.getvalue())

    def test_override_cannot_exceed_budget(self):
        # budget 下限是 MIN_INPUT_CHARS(1000)，--max-chars 再大叫也越不过去
        cfg = dict(CFG, budget={"max_input_chars": 50})
        with PipeHarness(raw=("y" * 5000).encode()) as harness:
            self.assertEqual(P.run_pipe_mode(["--max-chars", "9999", "q"], cfg), 0)
        self.assertIn("输入超过 1000 字符", harness.stderr.getvalue())
