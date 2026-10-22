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
UI_DEFAULTS = {"spinner": True, "fold_lines": 25}

# [thinking] 段：思考模式。DeepSeek 服务端默认**开启**（effort=high），但那套默认
# 与本工具的取舍相反：思考模式下服务端会忽略 temperature，而这里靠 temperature=0
# 换确定性；woc 要的是"一条命令 + 低延迟"，不是"多想半分钟"。所以默认关闭。
THINKING_DEFAULTS = {"enabled": False, "effort": "high"}
# 服务端还接受 none（等价于关闭）；这里只开放"思考力度"，关闭统一走 enabled。
THINKING_EFFORTS = ("low", "high", "max")

# [pipe] 段：标准输入不是终端时，把 stdin 当数据问答（只回答、不执行命令）。
# max_chars 默认 None：不单独配置时跟随 [budget] max_input_chars。
PIPE_DEFAULTS = {"enabled": True, "max_chars": None}

# [debug] 段：把 usage、前缀规模、输入规模等观测信息打到 stderr。
DEBUG_DEFAULTS = {"enabled": False}

# [session] 段：wocc 会话落盘，供 --last-session 恢复。
SESSION_DEFAULTS = {"persist": True, "max_messages": 40, "max_chars": 60000}

# [search] 段：网页搜索（DeepSeek Search API，见 docs/DESIGN-web-search.md）。
# enabled 只控制“模型能不能自己用 #SEARCH:”；人工输入的 :search 是显式动作，
# 不受它限制。base_url 留空时跟随根级 api_base（自动补 /v1）。
SEARCH_DEFAULTS = {
    "enabled": False,
    "base_url": "",
    "count": 5,
    "max_searches": 1,
    "max_chars": 6000,
    "timeout": 10,
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


def _thinking_effort(value, notes, source):
    """思考力度只认 low/high/max；写错就回落默认值并提示，不静默。"""
    effort = str(value).strip().lower()
    if effort in THINKING_EFFORTS:
        return effort
    notes.append(f"{source}={effort!r} 非法（可选 "
                 f"{'/'.join(THINKING_EFFORTS)}），已忽略")
    return THINKING_DEFAULTS["effort"]


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


def fold_lines(cfg):
    """超过多少行折叠输出（[ui] fold_lines）。0 表示不折叠。"""
    settings = cfg.get("ui") or {}
    try:
        value = int(settings.get("fold_lines", UI_DEFAULTS["fold_lines"]))
    except (TypeError, ValueError):
        return UI_DEFAULTS["fold_lines"]
    return max(value, 0)


def thinking_enabled(cfg):
    """是否开启思考模式（[thinking] enabled）。"""
    return bool((cfg.get("thinking") or {}).get(
        "enabled", THINKING_DEFAULTS["enabled"]))


def thinking_effort(cfg):
    """思考力度（[thinking] effort）：low/high/max，仅在开启时下发。"""
    value = str((cfg.get("thinking") or {}).get(
        "effort", THINKING_DEFAULTS["effort"])).strip().lower()
    return value if value in THINKING_EFFORTS else THINKING_DEFAULTS["effort"]


def session_settings(cfg):
    """wocc 会话持久化设置（[session]）。坏值一律回落到默认值。"""
    settings = cfg.get("session") or {}

    def positive(key):
        try:
            value = int(settings.get(key, SESSION_DEFAULTS[key]))
        except (TypeError, ValueError):
            return SESSION_DEFAULTS[key]
        return value if value > 0 else SESSION_DEFAULTS[key]

    return {
        "persist": bool(settings.get("persist",
                                    SESSION_DEFAULTS["persist"])),
        "max_messages": positive("max_messages"),
        "max_chars": positive("max_chars"),
    }


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


def search_enabled(cfg):
    """模型是否可以用 `#SEARCH:` 自行搜索（[search] enabled / --search）。

    只控制模型的自主动作：用户输入的 `:search` 是显式请求，不受这里限制。
    """
    return bool((cfg.get("search") or {}).get(
        "enabled", SEARCH_DEFAULTS["enabled"]))


def search_settings(cfg):
    """[search] 段的规范化取值。坏值回落到默认，不让一次手滑毁掉整次会话。"""
    raw = cfg.get("search") or {}

    def positive(key):
        try:
            value = int(raw.get(key, SEARCH_DEFAULTS[key]))
        except (TypeError, ValueError):
            return SEARCH_DEFAULTS[key]
        return value if value > 0 else SEARCH_DEFAULTS[key]

    return {
        "enabled": bool(raw.get("enabled", SEARCH_DEFAULTS["enabled"])),
        "base_url": str(raw.get("base_url") or "").strip(),
        "count": max(1, min(positive("count"), 50)),
        "max_searches": positive("max_searches"),
        "max_chars": positive("max_chars"),
        "timeout": positive("timeout"),
    }


def search_max_chars(cfg, reserved_chars=0):
    """单次搜索注入上下文的最大字符数。

    和 [pipe] max_chars 同理：显式配置优先，但仍要被上下文预算夹住 —— 否则
    把 search.max_chars 调到 10 万，请求会直接撞模型窗口。
    """
    settings = search_settings(cfg)
    return min(settings["max_chars"],
               budget.input_char_limit(cfg, reserved_chars))


def load_config(path=None):
    """读取配置；环境变量优先于配置文件。

    支持 [prefix] 段：
        [prefix]
        alias = "qwe"
        woc_prefix = "qwe"
        wocc_prefix = "qwee"
        woc_chat_prefix = "qwe_chat"

    以及 [sudo] / [context] / [exec] / [ui] / [pipe] / [search] / [budget]
    / [thinking] / [debug] 段：
        [sudo]
        capture_output = false

        [context]
        ls_on_start = false

        [exec]
        syntax_check = true

        [ui]
        spinner = true
        fold_lines = 25

        [thinking]              # DeepSeek 服务端默认开启，这里默认关闭
        enabled = false
        effort = "high"         # low/high/max，仅 enabled 时下发

        [pipe]
        enabled = true
        max_chars = 32000        # 可选：默认跟随 budget.max_input_chars

        [search]                # 网页搜索；enabled 只控制模型能否自行 #SEARCH:
        enabled = false
        count = 5               # 1～50
        max_searches = 1        # 单回合模型最多搜几次
        max_chars = 6000        # 注入上下文的结果总字符上限
        timeout = 10            # 独立于根级 timeout
        base_url = ""           # 空=跟随 api_base，自动补 /v1

        [budget]
        context_tokens = 64000
        max_output_tokens = 4096
        max_input_chars = 32000

        [debug]
        enabled = false

        [session]
        persist = true
        max_messages = 40
        max_chars = 60000

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
        "session": {
            "persist": _env_bool("WOC_SESSION_PERSIST",
                                 SESSION_DEFAULTS["persist"]),
            "max_messages": _env_positive_int(
                "WOC_SESSION_MAX_MESSAGES",
                SESSION_DEFAULTS["max_messages"], notes),
            "max_chars": _env_positive_int(
                "WOC_SESSION_MAX_CHARS",
                SESSION_DEFAULTS["max_chars"], notes),
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
            "fold_lines": _env_positive_int(
                "WOC_FOLD_LINES", UI_DEFAULTS["fold_lines"], notes),
        },
        "thinking": {
            "enabled": _env_bool("WOC_THINKING", THINKING_DEFAULTS["enabled"]),
            "effort": _thinking_effort(
                os.environ.get("WOC_THINKING_EFFORT",
                               THINKING_DEFAULTS["effort"]),
                notes, "环境变量 WOC_THINKING_EFFORT"),
        },
        "pipe": {
            "enabled": _env_bool("WOC_PIPE", PIPE_DEFAULTS["enabled"]),
            "max_chars": _env_optional_positive_int(
                "WOC_PIPE_MAX_CHARS", notes),
        },
        "search": {
            "enabled": _env_bool("WOC_SEARCH", SEARCH_DEFAULTS["enabled"]),
            "base_url": os.environ.get("WOC_SEARCH_BASE_URL",
                                       SEARCH_DEFAULTS["base_url"]),
            "count": _env_positive_int(
                "WOC_SEARCH_COUNT", SEARCH_DEFAULTS["count"], notes),
            "max_searches": _env_positive_int(
                "WOC_SEARCH_MAX", SEARCH_DEFAULTS["max_searches"], notes),
            "max_chars": _env_positive_int(
                "WOC_SEARCH_MAX_CHARS", SEARCH_DEFAULTS["max_chars"], notes),
            "timeout": _env_positive_int(
                "WOC_SEARCH_TIMEOUT", SEARCH_DEFAULTS["timeout"], notes),
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
                    elif (key == "fold_lines"
                          and "WOC_FOLD_LINES" not in os.environ):
                        cfg["ui"]["fold_lines"] = _file_positive_int(
                            value, UI_DEFAULTS["fold_lines"], notes,
                            "ui.fold_lines")
                    continue
                if section == "thinking":
                    if key == "enabled" and "WOC_THINKING" not in os.environ:
                        cfg["thinking"]["enabled"] = _to_bool(value)
                    elif (key == "effort"
                          and "WOC_THINKING_EFFORT" not in os.environ):
                        cfg["thinking"]["effort"] = _thinking_effort(
                            value, notes, "配置项 thinking.effort")
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
                if section == "search":
                    if key == "enabled" and "WOC_SEARCH" not in os.environ:
                        cfg["search"]["enabled"] = _to_bool(value)
                    elif (key == "base_url"
                          and "WOC_SEARCH_BASE_URL" not in os.environ):
                        cfg["search"]["base_url"] = value
                    elif key == "count" and "WOC_SEARCH_COUNT" not in os.environ:
                        cfg["search"]["count"] = _file_positive_int(
                            value, SEARCH_DEFAULTS["count"], notes,
                            "search.count")
                    elif (key == "max_searches"
                          and "WOC_SEARCH_MAX" not in os.environ):
                        cfg["search"]["max_searches"] = _file_positive_int(
                            value, SEARCH_DEFAULTS["max_searches"], notes,
                            "search.max_searches")
                    elif (key == "max_chars"
                          and "WOC_SEARCH_MAX_CHARS" not in os.environ):
                        cfg["search"]["max_chars"] = _file_positive_int(
                            value, SEARCH_DEFAULTS["max_chars"], notes,
                            "search.max_chars")
                    elif (key == "timeout"
                          and "WOC_SEARCH_TIMEOUT" not in os.environ):
                        cfg["search"]["timeout"] = _file_positive_int(
                            value, SEARCH_DEFAULTS["timeout"], notes,
                            "search.timeout")
                    continue
                if section == "session":
                    if key == "persist" and "WOC_SESSION_PERSIST" not in os.environ:
                        cfg["session"]["persist"] = _to_bool(value)
                    elif (key == "max_messages"
                          and "WOC_SESSION_MAX_MESSAGES" not in os.environ):
                        cfg["session"]["max_messages"] = _file_positive_int(
                            value, SESSION_DEFAULTS["max_messages"], notes,
                            "session.max_messages")
                    elif (key == "max_chars"
                          and "WOC_SESSION_MAX_CHARS" not in os.environ):
                        cfg["session"]["max_chars"] = _file_positive_int(
                            value, SESSION_DEFAULTS["max_chars"], notes,
                            "session.max_chars")
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
