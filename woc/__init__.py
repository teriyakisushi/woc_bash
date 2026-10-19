"""woc 包的懒加载门面。

不要在 `__init__` 里 eager import 整个依赖图：那会让 `import woc.config` 被迫
拉起 colors（termios/tty）、llm、context，既拖慢启动，也让"只读配置"的工具
（compile.py）被迫依赖整包可导入。属性访问时才导入对应模块。
"""

import importlib

_EXPORTS = {
    # colors
    "C": ".colors",
    "cerr": ".colors",
    "cout": ".colors",
    "eprint": ".colors",
    "interactive_input": ".colors",
    # config
    "capture_sudo": ".config",
    "load_config": ".config",
    "ls_on_start": ".config",
    "prefix": ".config",
    # context
    "Template": ".context",
    "build_messages": ".context",
    "lint_templates": ".context",
    "load_templates": ".context",
    "messages_prefix": ".context",
    "render_environment": ".context",
    "render_listing": ".context",
    "render_templates": ".context",
    "select_templates": ".context",
    "stable_context": ".context",
    # exec
    "format_exec_result": ".exec",
    "format_foreground_result": ".exec",
    "needs_privilege": ".exec",
    "run_command_capture": ".exec",
    "run_command_foreground": ".exec",
    "sudo_ticket_valid": ".exec",
    "syntax_error": ".exec",
    # llm
    "LLMError": ".llm",
    "StreamFilter": ".llm",
    "StreamMode": ".llm",
    "call_llm": ".llm",
    "call_llm_messages": ".llm",
    "call_llm_messages_stream": ".llm",
    "clean_text": ".llm",
    # prompts
    "MULTI_TURN_HINT": ".prompts",
    "SYSTEM_PROMPT": ".prompts",
    "render_system_prompt": ".prompts",
    # sanitize
    "ChatResponse": ".sanitize",
    "CommandGenError": ".sanitize",
    "is_dangerous": ".sanitize",
    "is_root_shell": ".sanitize",
    "sanitize": ".sanitize",
    "strip_dsml": ".sanitize",
    # ui
    "Spinner": ".ui",
}


def __getattr__(name):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value          # 缓存，后续访问走普通属性
    return value


def __dir__():
    return sorted(_EXPORTS)
