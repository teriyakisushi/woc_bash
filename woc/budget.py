"""预算：上下文窗口 / 回复上限 / 单次输入上限，三者互相约束。

以前是三个互不相干的旋钮（output_maxtokens、[pipe] max_chars、loop total_chars），
很容易配出"输入上限比模型窗口还大"或"回复预留根本没算"。这里统一换算：

- context_tokens / max_output_tokens 单位是 token（API 的单位）
- max_input_chars 单位是字符（人能数的单位）

换算系数取保守值：中文约 1 字/token，英文约 3-4 字符/token，这里按 2 字符/token，
宁可少塞一点，也不要撞上下文上限。
"""

# 保守换算：越多字符 → 越多 token，这里按 2 字符/token 估。
CHARS_PER_TOKEN = 2

# 再紧也要给用户留一点能问东西的空间。
MIN_INPUT_CHARS = 1000

CONTEXT_TOKENS = 64000
MAX_OUTPUT_TOKENS = 4096
MAX_INPUT_CHARS = 32000


def _positive(value, default):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def context_tokens(cfg):
    """模型上下文窗口（[budget] context_tokens）。"""
    return _positive((cfg.get("budget") or {}).get("context_tokens"),
                     CONTEXT_TOKENS)


def output_tokens(cfg):
    """单次回复上限（[budget] max_output_tokens；兼容旧的根级 output_maxtokens）。"""
    settings = cfg.get("budget") or {}
    if settings.get("max_output_tokens") is not None:
        return _positive(settings.get("max_output_tokens"), MAX_OUTPUT_TOKENS)
    return _positive(cfg.get("output_maxtokens"), MAX_OUTPUT_TOKENS)


def max_input_chars(cfg):
    """单次输入上限（[budget] max_input_chars）。"""
    return _positive((cfg.get("budget") or {}).get("max_input_chars"),
                     MAX_INPUT_CHARS)


def input_char_limit(cfg, reserved_chars=0):
    """扣掉回复预留与 system 段后，这次请求最多还能塞多少字符。

    reserved_chars 是已经确定要放进 context 的字符数（system prompt + environment），
    只有真正往请求里追加内容（管道输入）的地方才需要传。
    """
    reserved_tokens = (max(reserved_chars, 0) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
    room = context_tokens(cfg) - output_tokens(cfg) - reserved_tokens
    room = max(room, MIN_INPUT_CHARS // CHARS_PER_TOKEN)
    return max(MIN_INPUT_CHARS, min(max_input_chars(cfg),
                                    room * CHARS_PER_TOKEN))


def describe(cfg, reserved_chars=0):
    """一行摘要，给 --debug 用。"""
    return (f"budget: context={context_tokens(cfg)}tok "
            f"output={output_tokens(cfg)}tok "
            f"input<={max_input_chars(cfg)}chars "
            f"effective_input<={input_char_limit(cfg, reserved_chars)}chars")
