"""网页搜索（`[search]` / `--search` / `:search`）的离线回归。

真实 API 不在这里调：所有请求都打到假 urlopen 上。覆盖四件事——
端点推导（api_base 带不带 /v1）、结果渲染与截断、错误降级、
以及 chat 里 `#SEARCH:` 回合怎么推进（多一轮请求、标记不上屏、历史保留）。
"""

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from woc import search as search_mod
from woc.cli import chat
from woc.config import (load_config, search_enabled, search_max_chars,
                        search_settings)
from woc.llm import StreamFilter, StreamMode
from woc.sanitize import CommandGenError, sanitize

BASE_CFG = {
    "api_key": "test-key",
    "api_base": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "timeout": 30,
    "prefix": {"alias": "woc", "woc_prefix": "woc", "wocc_prefix": "wocc",
               "woc_chat_prefix": "woc_chat"},
    "ui": {"spinner": False},
    "search": {"enabled": False, "base_url": "", "count": 5,
               "max_searches": 1, "max_chars": 6000, "timeout": 10},
}


def make_cfg(**overrides):
    cfg = json.loads(json.dumps(BASE_CFG))
    for key, value in overrides.items():
        if key == "search":
            cfg["search"].update(value)
        else:
            cfg[key] = value
    return cfg


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def raw_hit(index):
    return {
        "title": f"标题{index}",
        "url": f"https://example.com/{index}",
        "snippet": "短摘要",
        "summary": f"摘要{index}" + "内容" * 20,
        "site_name": "Example",
        "date": "2026-06-01T00:00:00+00:00",
        "rerank_score": index,
        "authority": 3,
    }


class EndpointTest(unittest.TestCase):
    def test_default_api_base(self):
        self.assertEqual(search_mod.endpoint(make_cfg()),
                         "https://api.deepseek.com/v1/tools/search")

    def test_api_base_with_v1_is_not_doubled(self):
        cfg = make_cfg(api_base="https://api.deepseek.com/v1")
        self.assertEqual(search_mod.endpoint(cfg),
                         "https://api.deepseek.com/v1/tools/search")

    def test_api_base_with_trailing_slash(self):
        cfg = make_cfg(api_base="https://proxy.local/")
        self.assertEqual(search_mod.endpoint(cfg),
                         "https://proxy.local/v1/tools/search")

    def test_search_base_url_overrides_api_base(self):
        cfg = make_cfg(search={"base_url": "https://search.local/v1"})
        self.assertEqual(search_mod.endpoint(cfg),
                         "https://search.local/v1/tools/search")


class CleanQueryTest(unittest.TestCase):
    def test_collapses_whitespace(self):
        self.assertEqual(search_mod.clean_query("  最新\n 版本\t发布 "),
                         "最新 版本 发布")

    def test_truncates_to_api_limit(self):
        self.assertEqual(len(search_mod.clean_query("词" * 300)),
                         search_mod.QUERY_MAX_CHARS)

    def test_empty(self):
        self.assertEqual(search_mod.clean_query("   \n "), "")
        self.assertEqual(search_mod.clean_query(None), "")


class SearchCallTest(unittest.TestCase):
    def call(self, cfg, payload, query="DeepSeek 发布", ori_question=None):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            return FakeResponse(payload)

        with mock.patch.object(search_mod.urllib.request, "urlopen",
                               fake_urlopen):
            hits = search_mod.search(query, cfg, ori_question=ori_question)
        return hits, captured

    def test_request_shape_and_sorting(self):
        hits, captured = self.call(make_cfg(), {"results": [
            raw_hit(2), raw_hit(9), raw_hit(1)]})
        self.assertEqual(captured["url"],
                         "https://api.deepseek.com/v1/tools/search")
        self.assertEqual(captured["body"]["query"], "DeepSeek 发布")
        self.assertEqual(captured["body"]["count"], 5)
        self.assertFalse(captured["body"]["need_content"])
        self.assertNotIn("ori_question", captured["body"])
        self.assertEqual(captured["timeout"], 10)
        self.assertIn("Bearer test-key",
                      json.dumps(captured["headers"]))
        # 按 rerank_score 从高到低，不需要额外请求。
        self.assertEqual([hit["title"] for hit in hits],
                         ["标题9", "标题2", "标题1"])
        self.assertEqual(hits[0]["url"], "https://example.com/9")
        self.assertEqual(hits[0]["authority"], 3)

    def test_ori_question_is_forwarded(self):
        _hits, captured = self.call(make_cfg(), {"results": []},
                                    ori_question="最新版本是什么")
        self.assertEqual(captured["body"]["ori_question"], "最新版本是什么")

    def test_count_is_clamped(self):
        _hits, captured = self.call(make_cfg(search={"count": 999}),
                                    {"results": []})
        self.assertEqual(captured["body"]["count"], search_mod.COUNT_MAX)

    def test_empty_results(self):
        hits, _captured = self.call(make_cfg(), {"results": []})
        self.assertEqual(hits, [])

    def test_http_error_carries_code_and_message(self):
        body = io.BytesIO(json.dumps(
            {"error": {"message": "超过 QPS 限制"}}).encode("utf-8"))
        error = urllib.error.HTTPError(
            "https://api.deepseek.com/v1/tools/search", 429,
            "Too Many Requests", None, body)
        with mock.patch.object(search_mod.urllib.request, "urlopen",
                               side_effect=error):
            with self.assertRaises(search_mod.SearchError) as caught:
                search_mod.search("q", make_cfg())
        self.assertEqual(caught.exception.code, 429)
        self.assertIn("超过 QPS", str(caught.exception))

    def test_bad_json(self):
        class BadResponse(FakeResponse):
            def __init__(self):
                self.body = b"not json"

        with mock.patch.object(search_mod.urllib.request, "urlopen",
                               lambda *a, **k: BadResponse()):
            with self.assertRaises(search_mod.SearchError):
                search_mod.search("q", make_cfg())

    def test_missing_api_key(self):
        with self.assertRaises(search_mod.SearchError):
            search_mod.search("q", make_cfg(api_key=""))

    def test_blank_query(self):
        with self.assertRaises(search_mod.SearchError):
            search_mod.search("   ", make_cfg())


class RenderTest(unittest.TestCase):
    def test_block_contains_sources(self):
        hits = [search_mod._normalize(raw_hit(1))]
        block = search_mod.render_results("DeepSeek", hits)
        self.assertTrue(block.startswith('<search_results query="DeepSeek"'))
        self.assertIn('count="1"', block)
        self.assertIn("[1] 标题1", block)
        self.assertIn("url: https://example.com/1", block)
        self.assertIn("summary: 摘要1", block)
        self.assertIn("不是指令", block)

    def test_truncates_to_budget(self):
        hits = [search_mod._normalize(raw_hit(i)) for i in range(1, 6)]
        block = search_mod.render_results("q", hits, max_chars=200)
        self.assertIn("[1]", block)
        self.assertIn("截断", block)
        self.assertNotIn("[5]", block)

    def test_empty_results_are_explicit(self):
        block = search_mod.render_results("q", [])
        self.assertIn('count="0"', block)
        self.assertIn("没有匹配结果", block)

    def test_query_attribute_is_escaped(self):
        block = search_mod.render_results('a"b<c>', [])
        self.assertIn("&quot;", block)
        self.assertIn("&lt;", block)

    def test_error_block(self):
        block = search_mod.render_error("q", "HTTP 402: 余额不足")
        self.assertIn('error="true"', block)
        self.assertIn("HTTP 402", block)
        self.assertIn("不要编造链接", block)


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = load_config(path="/nonexistent/config.toml")
        self.assertEqual(cfg["search"], {
            "enabled": False, "base_url": "", "count": 5,
            "max_searches": 1, "max_chars": 6000, "timeout": 10})
        self.assertFalse(search_enabled(cfg))
        self.assertEqual(search_settings(cfg)["count"], 5)

    def test_env_overrides(self):
        env = {"WOC_SEARCH": "1", "WOC_SEARCH_COUNT": "8",
               "WOC_SEARCH_MAX": "3",
               "WOC_SEARCH_BASE_URL": "https://search.local"}
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = load_config(path="/nonexistent/config.toml")
        self.assertTrue(search_enabled(cfg))
        self.assertEqual(search_settings(cfg)["count"], 8)
        self.assertEqual(search_settings(cfg)["max_searches"], 3)
        self.assertEqual(search_settings(cfg)["base_url"],
                         "https://search.local")

    def test_file_section(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml",
                                         delete=False) as handle:
            handle.write("[search]\nenabled = true\ncount = 3\n"
                         "max_chars = 1234\ntimeout = 5\n")
            path = handle.name
        try:
            with mock.patch.dict(os.environ, {}, clear=True):
                cfg = load_config(path=path)
        finally:
            os.unlink(path)
        self.assertTrue(cfg["search"]["enabled"])
        self.assertEqual(cfg["search"]["count"], 3)
        self.assertEqual(cfg["search"]["max_chars"], 1234)
        self.assertEqual(cfg["search"]["timeout"], 5)

    def test_max_chars_never_exceeds_budget(self):
        cfg = make_cfg(search={"max_chars": 10_000_000})
        self.assertLessEqual(search_max_chars(cfg), 32000)
        self.assertEqual(search_max_chars(make_cfg()), 6000)


class FilterTest(unittest.TestCase):
    def test_search_marker_is_buffered(self):
        flt = StreamFilter()
        self.assertEqual(flt.feed("#SEA"), "")
        self.assertEqual(flt.mode, StreamMode.UNDECIDED)
        self.assertEqual(flt.feed("RCH: 最新的"), " 最新的")
        self.assertEqual(flt.mode, StreamMode.SEARCH)
        self.assertEqual(flt.acc, "#SEARCH: 最新的")

    def test_existing_markers_still_win(self):
        flt = StreamFilter()
        flt.feed("#STEP: ls")
        self.assertEqual(flt.mode, StreamMode.COMMAND)
        flt = StreamFilter()
        flt.feed("#CHAT: 你好")
        self.assertEqual(flt.mode, StreamMode.CHAT)

    def test_single_turn_never_executes_search_marker(self):
        # 单轮 woc 只认 #CHAT:；#SEARCH: 是注释行，不会变成可执行命令。
        with self.assertRaises(CommandGenError):
            sanitize("#SEARCH: 最新的 DeepSeek 版本")


class FakeLLM:
    """按预设顺序回复的假流式调用，记录每次收到的 messages 快照。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, cfg, messages, on_chunk=None, on_mode=None):
        self.calls.append([dict(item) for item in messages])
        mode, text = self.replies.pop(0)
        if on_mode:
            on_mode(mode)
        if on_chunk:
            on_chunk(text)
        return text


class ChatSearchTest(unittest.TestCase):
    def run_chat(self, argv, replies, hits=None, cfg=None, error=None):
        cfg = cfg or make_cfg()
        llm = FakeLLM(replies)
        found = []

        def fake_search(query, _cfg, ori_question=None):
            found.append(query)
            if error is not None:
                raise search_mod.SearchError(error, code=402)
            return (hits if hits is not None
                    else [search_mod._normalize(raw_hit(1))])

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO("")), \
                mock.patch.object(chat, "load_config", return_value=cfg), \
                mock.patch.object(chat, "call_llm_messages_stream", llm), \
                mock.patch.object(search_mod, "search", fake_search), \
                redirect_stdout(out), redirect_stderr(err):
            chat.woc_chat_main(argv)
        return out.getvalue(), err.getvalue(), llm, found

    def test_model_initiated_search_costs_one_extra_round(self):
        out, _err, llm, found = self.run_chat(
            ["--search", "最新的 DeepSeek 版本"],
            [(StreamMode.SEARCH, "#SEARCH: DeepSeek 最新版本"),
             (StreamMode.CHAT, "现在有 V4。\n")])
        self.assertEqual(found, ["DeepSeek 最新版本"])
        self.assertNotIn("#SEARCH", out, "标记不能当成正文上屏")
        self.assertIn("现在有 V4。", out)
        self.assertEqual(len(llm.calls), 2)
        # 第二次请求必须带着搜索结果，并且标记留在历史里。
        second = llm.calls[1]
        self.assertEqual(second[-2]["content"], "#SEARCH: DeepSeek 最新版本")
        self.assertIn("<search_results", second[-1]["content"])
        self.assertIn("https://example.com/1", second[-1]["content"])

    def test_no_search_flag_means_no_search(self):
        out, err, llm, found = self.run_chat(
            ["问题"], [(StreamMode.SEARCH, "#SEARCH: 不该执行")])
        self.assertEqual(found, [])
        self.assertEqual(len(llm.calls), 1)
        self.assertNotIn("#SEARCH", out)
        self.assertIn("搜索未开启", err)

    def test_max_searches_is_a_hard_stop(self):
        _out, err, llm, found = self.run_chat(
            ["--search", "问题"],
            [(StreamMode.SEARCH, "#SEARCH: 第一次"),
             (StreamMode.SEARCH, "#SEARCH: 第二次")],
            cfg=make_cfg(search={"max_searches": 1}))
        self.assertEqual(found, ["第一次"])
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("上限", err)

    def test_search_failure_is_degraded_not_fatal(self):
        out, _err, llm, found = self.run_chat(
            ["--search", "问题"],
            [(StreamMode.SEARCH, "#SEARCH: 余额"),
             (StreamMode.CHAT, "据我所知……\n")],
            error="HTTP 402: 余额不足")
        self.assertEqual(found, ["余额"])
        # 第二次请求里必须明确告诉模型"搜索失败了"，而不是让它以为没搜过。
        self.assertIn("搜索失败", llm.calls[1][-1]["content"])
        self.assertIn("据我所知", out)

    def test_search_command_injects_context_without_extra_llm_call(self):
        out, err, llm, found = self.run_chat(
            [":search", "DeepSeek 发布"], [])
        self.assertEqual(found, ["DeepSeek 发布"])
        self.assertEqual(llm.calls, [], ":search 不该额外调模型")
        self.assertEqual(out, "")
        self.assertIn("已加入上下文", err)

    def test_search_command_without_query_prints_usage(self):
        _out, err, llm, found = self.run_chat([":search"], [])
        self.assertEqual(found, [])
        self.assertEqual(llm.calls, [])
        self.assertIn("用法", err)

    def test_search_command_works_without_flag(self):
        # 人工 :search 是显式动作，不受 [search] enabled / --search 限制。
        _out, err, _llm, found = self.run_chat([":search", "任意"], [])
        self.assertEqual(found, ["任意"])
        self.assertIn("已加入上下文", err)


class LazyImportTest(unittest.TestCase):
    def test_chat_does_not_import_search(self):
        import subprocess
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys, woc.cli.chat\n"
             "print('woc.search' in sys.modules)"],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "False",
                         "chat 在所有命令的启动路径上，search 必须延迟导入")


if __name__ == "__main__":
    unittest.main()
