"""安装器 compile.py 的离线回归。

重点两件事：
1. `_config_module()`：config.py 里有相对导入（budget / debug），安装器必须按包
   加载它，不能再用 spec_from_file_location 当单文件跑。
2. `merge_config_template()`：配置模板只补缺失项 —— 老配置的值不能动，
   新增的段/键要能自动落地。
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def load_compile_module():
    spec = importlib.util.spec_from_file_location(
        "woc_compile_under_test", os.path.join(ROOT, "compile.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConfigModuleTest(unittest.TestCase):
    def test_config_loads_as_package(self):
        module = load_compile_module()
        config = module._config_module()
        self.assertIn("alias", config.PREFIX_DEFAULTS)
        self.assertTrue(callable(config.load_config))

    def test_current_prefix_is_sanitized(self):
        module = load_compile_module()
        names = module.current_prefix()
        for key in ("alias", "woc_prefix", "wocc_prefix", "woc_chat_prefix"):
            self.assertIn(key, names)
            self.assertRegex(names[key], module.NAME_RE)


class ConfigTemplateTest(unittest.TestCase):
    def setUp(self):
        self.module = load_compile_module()

    def test_creates_file_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            self.assertEqual(self.module.merge_config_template(path), -1)
            text = path.read_text(encoding="utf-8")
        for section in ("[prefix]", "[loop]", "[ui]", "[thinking]", "[pipe]",
                        "[search]", "[budget]", "[debug]", "[session]"):
            self.assertIn(section, text)
        self.assertIn("enabled = true", text.split("[search]")[1].split("[")[0])

    def test_keeps_existing_values_and_fills_the_rest(self):
        original = ('api_key = "sk-keep"\n'
                    "[ui]\n"
                    "spinner = false\n")
        merged, added = self.module.merge_config_text(original)
        self.assertGreater(added, 0)
        self.assertIn('api_key = "sk-keep"', merged)
        self.assertIn("spinner = false", merged)
        self.assertNotIn("spinner = true", merged, "已有值不能被覆盖")
        self.assertIn("fold_lines = 25", merged, "同一段里缺的键要补上")
        self.assertIn("[search]", merged, "缺的段要追加")
        self.assertIn("[session]", merged)

    def test_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            self.module.merge_config_template(path)
            before = path.read_text(encoding="utf-8")
            self.assertEqual(self.module.merge_config_template(path), 0)
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_merged_file_is_loadable(self):
        """补完的配置必须能被 woc 的迷你 TOML 解析器读回来。"""
        config = self.module._config_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            self.module.merge_config_template(path)
            with mock.patch.dict(os.environ, {"WOC_API_KEY": ""},
                                          clear=True):
                cfg = config.load_config(path=str(path))
        self.assertEqual(cfg["ui"]["fold_lines"], 25)
        self.assertTrue(cfg["search"]["enabled"])
        self.assertEqual(cfg["loop"]["max_steps"], 5)
        self.assertEqual(cfg["budget"]["context_tokens"], 64000)


class ConfigTemplateConsistencyTest(unittest.TestCase):
    """模板必须和 config.py 的默认值一一对应。

    少一个键：老用户升级安装器也拿不到它（内置默认值兜着，但没人知道能配）；
    多一个键：没人消费，纯噪音。所以两边都按集合比。
    """

    EXPECTED = {
        "": {"api_base", "api_key", "model", "timeout"},
        "prefix": {"alias", "woc_prefix", "wocc_prefix", "woc_chat_prefix"},
        "sudo": {"capture_output"},
        "context": {"ls_on_start"},
        "loop": {"enabled", "max_steps", "total_chars", "auto_safe"},
        "exec": {"syntax_check"},
        "ui": {"spinner", "fold_lines"},
        "thinking": {"enabled", "effort"},
        "pipe": {"enabled"},
        "search": {"enabled", "base_url", "count", "max_searches",
                   "max_chars", "timeout", "query_by_llm"},
        "budget": {"context_tokens", "max_output_tokens", "max_input_chars"},
        "debug": {"enabled"},
        "session": {"persist", "max_messages", "max_chars"},
    }
    # 默认值表示"跟随别的配置"的键：写死进文件反而会覆盖掉跟随关系。
    OPTIONAL = {("pipe", "max_chars")}

    def test_template_keys_match_defaults(self):
        module = load_compile_module()
        blocks = {name: {key for key, _line in entries}
                  for name, entries in module._template_sections()}
        expected = {name: set(keys) for name, keys in self.EXPECTED.items()}
        for name, key in self.OPTIONAL:
            expected[name].discard(key)
        self.assertEqual(blocks, expected)

    def test_template_values_are_the_defaults(self):
        """模板里的值必须等于内置默认值，否则"补进配置"会悄悄改行为。"""
        module = load_compile_module()
        config = module._config_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            module.merge_config_template(path)
            with mock.patch.dict(os.environ, {"WOC_API_KEY": ""}, clear=True):
                from_template = config.load_config(path=str(path))
                defaults = config.load_config(path="/nonexistent/config.toml")
        self.assertEqual(from_template, defaults)


if __name__ == "__main__":
    unittest.main()
