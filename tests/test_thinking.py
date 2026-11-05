"""思考模式开关（[thinking]）。

DeepSeek 服务端默认**开启**思考模式（effort 默认 high），而本工具的取舍相反：
要的是"一条命令 + 低延迟"，且思考模式下服务端会忽略 temperature —— 与这里
temperature=0 的确定性设计冲突。所以默认关闭，并且**总是显式下发** thinking 字段，
免得哪天服务端改了默认值，行为跟着漂。

顺带守住：payload 里关掉时不带 reasoning_effort，开启时才带。
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from woc import debug
from woc.config import load_config, thinking_effort, thinking_enabled
from woc.llm import call_llm_messages_stream


class _Response:
    """假 SSE 响应：一条 content delta + [DONE]。"""

    def __init__(self):
        self._lines = [
            b'data: {"choices": [{"delta": {"content": "#CHAT:hi"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        pass


def payload_for(cfg):
    """跑一次假的流式请求，把发给服务端的 JSON body 抓回来。"""
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response()

    with mock.patch.object(debug, "_enabled", False), \
            mock.patch("woc.llm.urllib.request.urlopen", fake_urlopen):
        call_llm_messages_stream([{"role": "user", "content": "hi"}], cfg)
    return seen["payload"]


class ThinkingConfigTest(unittest.TestCase):
    def load(self, text, env=None):
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8",
                                             suffix=".toml", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        with mock.patch.dict(os.environ, env or {}, clear=True):
            return load_config(path=handle.name)

    def test_default_is_off(self):
        cfg = self.load("")
        self.assertFalse(thinking_enabled(cfg))
        self.assertEqual(thinking_effort(cfg), "high")

    def test_file_section(self):
        cfg = self.load("[thinking]\nenabled = true\neffort = \"low\"\n")
        self.assertTrue(thinking_enabled(cfg))
        self.assertEqual(thinking_effort(cfg), "low")

    def test_env_overrides_file(self):
        cfg = self.load("[thinking]\nenabled = false\neffort = \"low\"\n",
                        env={"WOC_THINKING": "1", "WOC_THINKING_EFFORT": "max"})
        self.assertTrue(thinking_enabled(cfg))
        self.assertEqual(thinking_effort(cfg), "max")

    def test_bad_effort_falls_back_with_note(self):
        err = io.StringIO()
        with redirect_stderr(err):
            cfg = self.load("[thinking]\nenabled = true\neffort = \"ultra\"\n")
        self.assertEqual(thinking_effort(cfg), "high")
        self.assertIn("非法", err.getvalue())

    def test_missing_section_is_tolerated(self):
        # 老配置/测试里手搓的 cfg 没有 thinking 键，不能炸。
        self.assertFalse(thinking_enabled({}))
        self.assertEqual(thinking_effort({}), "high")


class ThinkingPayloadTest(unittest.TestCase):
    def cfg(self, enabled, effort="high"):
        return {
            "api_key": "test-key",
            "api_base": "http://127.0.0.1:9",
            "model": "deepseek-flash",
            "timeout": 5,
            "budget": {"context_tokens": 64000, "max_output_tokens": 4096,
                       "max_input_chars": 32000},
            "thinking": {"enabled": enabled, "effort": effort},
        }

    def test_disabled_sends_explicit_disabled(self):
        payload = payload_for(self.cfg(False))
        # 一定要发：不发等于"跟随服务端默认值"，而服务端默认是开启的。
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", payload)

    def test_enabled_sends_effort(self):
        payload = payload_for(self.cfg(True, "low"))
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "low")

    def test_bad_effort_never_leaks_to_api(self):
        payload = payload_for(self.cfg(True, "ultra"))
        self.assertEqual(payload["reasoning_effort"], "high")

    def test_default_cfg_disables_thinking(self):
        payload = payload_for(self.cfg(None))
        self.assertEqual(payload["thinking"], {"type": "disabled"})


if __name__ == "__main__":
    unittest.main()
