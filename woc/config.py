import os
import sys

# [prefix] 段的默认值。alias 用于日志/提示与 System Prompt；
# woc_prefix / wocc_prefix / woc_chat_prefix 是安装后的命令名。
# 注意：`woc -c` 跟随 woc_prefix，不能单独改名。
PREFIX_DEFAULTS = {
    "alias": "woc",
    "woc_prefix": "woc",
    "wocc_prefix": "wocc",
    "woc_chat_prefix": "woc_chat",
}

# 环境变量优先于配置文件：这些 key 在环境里出现时，文件里的值被忽略。
ENV_KEYS = {
    "api_base": "WOC_API_BASE",
    "api_key": "WOC_API_KEY",
    "model": "WOC_MODEL",
    "timeout": "WOC_TIMEOUT",
}

def _to_bool(value):
    return str(value).strip().lower() not in ("", "0", "false", "no", "off")


def _env_bool(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return _to_bool(raw)


def default_config_path():
    return os.path.expanduser("~/.config/woc/config.toml")


def _env_int(name, default, notes):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        notes.append(f"环境变量 {name}={raw!r} 非法，已忽略")
        return default


def prefix(cfg, key="woc_prefix"):
    """取 [prefix] 里的命令名/别名；缺失时回落到默认值。"""
    value = (cfg.get("prefix") or {}).get(key)
    return value or PREFIX_DEFAULTS.get(key, "woc")


def capture_sudo(cfg):
    """是否需要捕获需要密码的 sudo 命令输出（[sudo] capture_output）。"""
    return bool((cfg.get("sudo") or {}).get("capture_output"))


def load_config(path=None):
    """读取配置；环境变量优先于配置文件。

    支持 [prefix] 段：
        [prefix]
        alias = "qwe"
        woc_prefix = "qwe"
        wocc_prefix = "qwee"
        woc_chat_prefix = "qwe_chat"

    以及 [sudo] 段：
        [sudo]
        capture_output = false
    """
    notes = []
    cfg = {
        "api_base": os.environ.get("WOC_API_BASE", "https://api.deepseek.com"),
        "api_key": os.environ.get("WOC_API_KEY", ""),
        "model": os.environ.get("WOC_MODEL", "deepseek-flash"),
        "timeout": _env_int("WOC_TIMEOUT", 30, notes),
        "prefix": dict(PREFIX_DEFAULTS),
        "sudo": {"capture_output": _env_bool("WOC_CAPTURE_SUDO", False)},
    }

    path = path or default_config_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            section = ""
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1].strip().lower()
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if not key or not value:
                    continue
                if section == "prefix":
                    if key in PREFIX_DEFAULTS:
                        cfg["prefix"][key] = value
                    continue
                if section == "sudo":
                    if key == "capture_output" and "WOC_CAPTURE_SUDO" not in os.environ:
                        cfg["sudo"]["capture_output"] = _to_bool(value)
                    continue
                if section:
                    continue
                if key in ENV_KEYS and ENV_KEYS[key] in os.environ:
                    continue
                if key == "timeout":
                    try:
                        cfg[key] = int(value)
                    except ValueError:
                        notes.append(f"配置项 timeout 非法: {value!r}，已忽略")
                elif key in cfg:
                    cfg[key] = value
    except FileNotFoundError:
        pass
    except OSError as exc:
        notes.append(f"读取配置失败: {exc}")

    for note in notes:
        print(f"[{prefix(cfg, 'alias')}] {note}", file=sys.stderr)
    return cfg
