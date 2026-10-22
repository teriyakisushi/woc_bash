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


if __name__ == "__main__":
    unittest.main()
