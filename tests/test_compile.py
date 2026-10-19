"""安装器 compile.py 的离线回归。

重点是 `_config_module()`：config.py 里有相对导入（budget / debug），
安装器必须按包加载它，不能再用 spec_from_file_location 当单文件跑。
"""

import importlib.util
import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
