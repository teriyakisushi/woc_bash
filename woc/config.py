import os
import sys

from . import budget, debug

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
    "output_maxtokens": "WOC_OUTPUT_MAXTOKENS",
}

# 单次回复的最大 token 数（统一默认值在 budget 模块，这里保留旧名字）
DEFAULT_OUTPUT_MAXTOKENS = budget.MAX_OUTPUT_TOKENS

# [loop] 的默认值：默认关闭；只读命令默认仍要人工确认。
LOOP_DEFAULTS = {
    "enabled": False,
    "max_steps": 5,
    "total_chars": 24000,
    "auto_safe": False,
}

# [exec] 段：命令执行前的检查。syntax_check 用 bash -n 预检语法。
EXEC_DEFAULTS = {"syntax_check": True}

# [ui] 段：等待模型/执行命令时的 loading（仅 tty 生效）。
UI_DEFAULTS = {"spinner": True}

# [pipe] 段：标准输入不是终端时，把 stdin 当数据问答（只回答、不执行命令）。
# max_chars 默认 None：不单独配置时跟随 [budget] max_input_chars。
PIPE_DEFAULTS = {"enabled": True, "max_chars": None}

# [debug] 段：把 usage、前缀规模、输入规模等观测信息打到 stderr。
DEBUG_DEFAULTS = {"enabled": False}


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


def _env_positive_int(name, default, notes):
    value = _env_int(name, default, notes)
    if value <= 0:
        notes.append(f"环境变量 {name}={value!r} 必须大于 0，已忽略")
        return default
    return value


def _env_optional_positive_int(name, notes):
    """环境变量没设时返回 None（用于"跟随其他配置"的键）。"""
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        notes.append(f"环境变量 {name}={raw!r} 非法，已忽略")
        return None
    if value <= 0:
        notes.append(f"环境变量 {name}={value!r} 必须大于 0，已忽略")
        return None
    return value


def _file_positive_int(value, default, notes, name):
    try:
        number = int(value)
    except ValueError:
        notes.append(f"配置项 {name} 非法: {value!r}，已忽略")
        return default
    if number <= 0:
        notes.append(f"配置项 {name} 必须大于 0: {value!r}，已忽略")
        return default
    return number


def prefix(cfg, key="woc_prefix"):
    """取 [prefix] 里的命令名/别名；缺失时回落到默认值。"""
    value = (cfg.get("prefix") or {}).get(key)
    return value or PREFIX_DEFAULTS.get(key, "woc")


def capture_sudo(cfg):
    """是否需要捕获需要密码的 sudo 命令输出（[sudo] capture_output）。"""
    return bool((cfg.get("sudo") or {}).get("capture_output"))


def ls_on_start(cfg):
    """是否在启动/单轮调用时注入一次 `ls -alF` 目录快照（[context] ls_on_start）。"""
    return bool((cfg.get("context") or {}).get("ls_on_start"))


def syntax_check(cfg):
    """是否在确认前用 `bash -n` 预检命令语法（[exec] syntax_check）。"""
    return bool((cfg.get("exec") or {}).get(
        "syntax_check", EXEC_DEFAULTS["syntax_check"]))


def spinner(cfg):
    """是否显示等待模型/执行命令的 loading（[ui] spinner，仅 tty 生效）。"""
    return bool((cfg.get("ui") or {}).get("spinner", UI_DEFAULTS["spinner"]))


def pipe_enabled(cfg):
    """标准输入不是终端时，是否把 stdin 当数据问答（[pipe] enabled）。"""
    return bool((cfg.get("pipe") or {}).get("enabled", PIPE_DEFAULTS["enabled"]))


def pipe_max_chars(cfg, reserved_chars=0):
    """管道模式单次送入模型的最大字符数。

    显式的 [pipe] max_chars 优先，但仍要被上下文预算夹住 —— 否则用户把输入
    上限调到 100 万，请求会直接撞模型窗口。没配时直接用 [budget]。
    """
    settings = cfg.get("pipe") or {}
    limit = budget.input_char_limit(cfg, reserved_chars)
    explicit = settings.get("max_chars")
    if explicit is None:
        return limit
    try:
        value = int(explicit)
    except (TypeError, ValueError):
        return limit
    if value <= 0:
        return limit
    return min(value, limit)


def load_config(path=None):
    """读取配置；环境变量优先于配置文件。

    支持 [prefix] 段：
        [prefix]
        alias = "qwe"
        woc_prefix = "qwe"
        wocc_prefix = "qwee"
        woc_chat_prefix = "qwe_chat"

    以及 [sudo] / [context] / [exec] / [ui] / [pipe] / [budget] / [debug] 段：
        [sudo]
        capture_output = false

        [context]
        ls_on_start = false

        [exec]
        syntax_check = true

        [ui]
        spinner = true

        [pipe]
        enabled = true
        max_chars = 32000        # 可选：默认跟随 budget.max_input_chars

        [budget]
        context_tokens = 64000
        max_output_tokens = 4096
        max_input_chars = 32000

        [debug]
        enabled = false

    根级 output_maxtokens 是 [budget] max_output_tokens 的旧名字，仍然可用。
    """
    notes = []
    # 回复上限：新键优先，兼容旧的 WOC_OUTPUT_MAXTOKENS。
    output_env = None
    for env_name in ("WOC_MAX_OUTPUT_TOKENS", "WOC_OUTPUT_MAXTOKENS"):
        if env_name in os.environ:
            output_env = _env_positive_int(
                env_name, DEFAULT_OUTPUT_MAXTOKENS, notes)
            break
    cfg = {
        "api_base": os.environ.get("WOC_API_BASE", "https://api.deepseek.com"),
        "api_key": os.environ.get("WOC_API_KEY", ""),
        "model": os.environ.get("WOC_MODEL", "deepseek-flash"),
        "timeout": _env_int("WOC_TIMEOUT", 30, notes),
        "budget": {
            "context_tokens": _env_positive_int(
                "WOC_CONTEXT_TOKENS", budget.CONTEXT_TOKENS, notes),
            "max_output_tokens": (output_env if output_env is not None
                                  else budget.MAX_OUTPUT_TOKENS),
            "max_input_chars": _env_positive_int(
                "WOC_MAX_INPUT_CHARS", budget.MAX_INPUT_CHARS, notes),
        },
        "debug": {
            "enabled": _env_bool("WOC_DEBUG", DEBUG_DEFAULTS["enabled"]),
        },
        "prefix": dict(PREFIX_DEFAULTS),
        "sudo": {"capture_output": _env_bool("WOC_CAPTURE_SUDO", False)},
        "context": {"ls_on_start": _env_bool("WOC_CONTEXT_LS", False)},
        "loop": {
            "enabled": _env_bool("WOC_LOOP", LOOP_DEFAULTS["enabled"]),
            "max_steps": _env_positive_int(
                "WOC_LOOP_MAX_STEPS", LOOP_DEFAULTS["max_steps"], notes),
            "total_chars": _env_positive_int(
                "WOC_LOOP_TOTAL_CHARS", LOOP_DEFAULTS["total_chars"], notes),
            "auto_safe": _env_bool("WOC_LOOP_AUTO_SAFE", LOOP_DEFAULTS["auto_safe"]),
        },
        "exec": {
            "syntax_check": _env_bool(
                "WOC_SYNTAX_CHECK", EXEC_DEFAULTS["syntax_check"]),
        },
        "ui": {
            "spinner": _env_bool("WOC_SPINNER", UI_DEFAULTS["spinner"]),
        },
        "pipe": {
            "enabled": _env_bool("WOC_PIPE", PIPE_DEFAULTS["enabled"]),
            "max_chars": _env_optional_positive_int(
                "WOC_PIPE_MAX_CHARS", notes),
        },
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
                if section == "context":
                    if key == "ls_on_start" and "WOC_CONTEXT_LS" not in os.environ:
                        cfg["context"]["ls_on_start"] = _to_bool(value)
                    continue
                if section == "loop":
                    if key == "enabled" and "WOC_LOOP" not in os.environ:
                        cfg["loop"]["enabled"] = _to_bool(value)
                    elif key == "auto_safe" and "WOC_LOOP_AUTO_SAFE" not in os.environ:
                        cfg["loop"]["auto_safe"] = _to_bool(value)
                    elif key == "max_steps" and "WOC_LOOP_MAX_STEPS" not in os.environ:
                        cfg["loop"]["max_steps"] = _file_positive_int(
                            value, LOOP_DEFAULTS["max_steps"], notes, "loop.max_steps")
                    elif key == "total_chars" and "WOC_LOOP_TOTAL_CHARS" not in os.environ:
                        cfg["loop"]["total_chars"] = _file_positive_int(
                            value, LOOP_DEFAULTS["total_chars"], notes, "loop.total_chars")
                    continue
                if section == "exec":
                    if key == "syntax_check" and "WOC_SYNTAX_CHECK" not in os.environ:
                        cfg["exec"]["syntax_check"] = _to_bool(value)
                    continue
                if section == "ui":
                    if key == "spinner" and "WOC_SPINNER" not in os.environ:
                        cfg["ui"]["spinner"] = _to_bool(value)
                    continue
                if section == "budget":
                    if (key == "context_tokens"
                            and "WOC_CONTEXT_TOKENS" not in os.environ):
                        cfg["budget"]["context_tokens"] = _file_positive_int(
                            value, budget.CONTEXT_TOKENS, notes,
                            "budget.context_tokens")
                    elif (key == "max_output_tokens"
                          and output_env is None):
                        cfg["budget"]["max_output_tokens"] = _file_positive_int(
                            value, budget.MAX_OUTPUT_TOKENS, notes,
                            "budget.max_output_tokens")
                    elif (key == "max_input_chars"
                          and "WOC_MAX_INPUT_CHARS" not in os.environ):
                        cfg["budget"]["max_input_chars"] = _file_positive_int(
                            value, budget.MAX_INPUT_CHARS, notes,
                            "budget.max_input_chars")
                    continue
                if section == "debug":
                    if key == "enabled" and "WOC_DEBUG" not in os.environ:
                        cfg["debug"]["enabled"] = _to_bool(value)
                    continue
                if section == "pipe":
                    if key == "enabled" and "WOC_PIPE" not in os.environ:
                        cfg["pipe"]["enabled"] = _to_bool(value)
                    elif (key == "max_chars"
                          and "WOC_PIPE_MAX_CHARS" not in os.environ):
                        cfg["pipe"]["max_chars"] = _file_positive_int(
                            value, PIPE_DEFAULTS["max_chars"], notes,
                            "pipe.max_chars")
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
                elif key == "output_maxtokens":
                    if output_env is None:
                        cfg["budget"]["max_output_tokens"] = _file_positive_int(
                            value, DEFAULT_OUTPUT_MAXTOKENS, notes, key)
                elif key in cfg:
                    cfg[key] = value
    except FileNotFoundError:
        pass
    except OSError as exc:
        notes.append(f"读取配置失败: {exc}")

    # 旧读取方（llm.py 之前）仍然能拿到这个键。
    cfg["output_maxtokens"] = cfg["budget"]["max_output_tokens"]
    debug.configure(cfg)

    for note in notes:
        print(f"[{prefix(cfg, 'alias')}] {note}", file=sys.stderr)
    return cfg
