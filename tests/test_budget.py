"""预算与 debug 观测的离线测试。

守两件事：
1. 三个数字（context / output / input）互相约束，配得再离谱也不会越过模型窗口；
2. debug 输出只走 stderr，且默认关闭（关闭时一行都不能有）。
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from woc import budget, debug
from woc.config import load_config, pipe_max_chars


class BudgetTest(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(budget.context_tokens({}), 64000)
        self.assertEqual(budget.output_tokens({}), 4096)
        self.assertEqual(budget.max_input_chars({}), 32000)
        self.assertEqual(budget.input_char_limit({}), 32000)

    def test_context_clamps_input(self):
        cfg = {"budget": {"context_tokens": 8000, "max_output_tokens": 4096}}
        # (8000 - 4096) tokens * 2 chars = 7808
        self.assertEqual(budget.input_char_limit(cfg), 7808)

    def test_configured_input_is_capped_by_context(self):
        cfg = {"budget": {"context_tokens": 8000,
                          "max_output_tokens": 4096,
                          "max_input_chars": 100000}}
        self.assertEqual(budget.input_char_limit(cfg), 7808)

    def test_reserved_chars_are_subtracted(self):
        cfg = {"budget": {"context_tokens": 5000, "max_output_tokens": 1000}}
        # (5000 - 1000 - 500/2) * 2 = 7500
        self.assertEqual(budget.input_char_limit(cfg, reserved_chars=500), 7500)

    def test_never_below_minimum(self):
        cfg = {"budget": {"context_tokens": 100, "max_output_tokens": 4096}}
        self.assertGreaterEqual(budget.input_char_limit(cfg), budget.MIN_INPUT_CHARS)

    def test_legacy_output_key(self):
        self.assertEqual(budget.output_tokens({"output_maxtokens": 128}), 128)
        cfg = {"output_maxtokens": 128,
               "budget": {"max_output_tokens": 256}}
        self.assertEqual(budget.output_tokens(cfg), 256)

    def test_pipe_max_chars_follows_budget(self):
        self.assertEqual(pipe_max_chars({}), 32000)
        cfg = {"budget": {"max_input_chars": 5000}}
        self.assertEqual(pipe_max_chars(cfg), 5000)

    def test_pipe_explicit_value_is_still_clamped(self):
        cfg = {"budget": {"max_input_chars": 5000}}
        self.assertEqual(pipe_max_chars(dict(cfg, pipe={"max_chars": 9000})), 5000)
        self.assertEqual(pipe_max_chars(dict(cfg, pipe={"max_chars": 100})), 100)

    def test_describe(self):
        line = budget.describe({})
        self.assertIn("context=64000tok", line)
        self.assertIn("output=4096tok", line)
        self.assertIn("input<=32000chars", line)


class DebugTest(unittest.TestCase):
    def tearDown(self):
        debug.configure({})

    def test_disabled_by_default(self):
        debug.configure({})
        err = io.StringIO()
        with redirect_stderr(err):
            debug.log("should not appear")
        self.assertEqual(err.getvalue(), "")

    def test_enabled_writes_to_stderr_with_alias(self):
        debug.configure({"debug": {"enabled": True},
                         "prefix": {"alias": "qwe"}})
        err = io.StringIO()
        with redirect_stderr(err):
            debug.log("hello")
        self.assertEqual(err.getvalue(), "[qwe] debug: hello\n")

    def test_usage_line_with_cache_fields(self):
        line = debug.usage_line({
            "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
            "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20,
        })
        self.assertIn("prompt=100", line)
        self.assertIn("cache_hit=80", line)
        self.assertIn("cache_miss=20", line)
        self.assertIn("completion=20", line)

    def test_usage_line_without_usage(self):
        self.assertIn("未返回", debug.usage_line(None))


class ConfigIntegrationTest(unittest.TestCase):
    def load(self, text, env=None):
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8",
                                             suffix=".toml", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        with mock.patch.dict(os.environ, env or {}, clear=True):
            return load_config(path=handle.name)

    def test_budget_section(self):
        cfg = self.load("[budget]\ncontext_tokens = 32000\n"
                        "max_output_tokens = 2048\nmax_input_chars = 8000\n")
        self.assertEqual(cfg["budget"]["context_tokens"], 32000)
        self.assertEqual(cfg["budget"]["max_output_tokens"], 2048)
        self.assertEqual(cfg["budget"]["max_input_chars"], 8000)
        self.assertEqual(cfg["output_maxtokens"], 2048)

    def test_legacy_output_maxtokens_still_works(self):
        cfg = self.load("output_maxtokens = 512\n")
        self.assertEqual(cfg["budget"]["max_output_tokens"], 512)

    def test_budget_section_overrides_legacy(self):
        cfg = self.load("output_maxtokens = 512\n\n[budget]\nmax_output_tokens = 1024\n")
        self.assertEqual(cfg["budget"]["max_output_tokens"], 1024)

    def test_debug_section(self):
        self.addCleanup(debug.configure, {})
        cfg = self.load("[debug]\nenabled = true\n")
        self.assertTrue(cfg["debug"]["enabled"])
        self.assertTrue(debug.enabled())

    def test_env_wins_over_file(self):
        cfg = self.load("[budget]\nmax_output_tokens = 2048\n",
                        env={"WOC_MAX_OUTPUT_TOKENS": "777"})
        self.assertEqual(cfg["budget"]["max_output_tokens"], 777)


if __name__ == "__main__":
    unittest.main()
