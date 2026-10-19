"""WOC_DEBUG=1 时的观测输出。

约定：
- 只写 stderr，绝不污染 stdout（管道模式可以继续 `| less` / `> out.md`）；
- 前缀统一 `[alias] debug:`，不硬编码 `[woc]`；
- 默认关闭，关闭时零开销（一次布尔判断）。
"""

import sys

_enabled = False
_alias = "woc"


def configure(cfg):
    """由 load_config 在解析完成后调用。"""
    global _enabled, _alias
    _enabled = bool((cfg.get("debug") or {}).get("enabled"))
    _alias = (cfg.get("prefix") or {}).get("alias") or "woc"


def enabled():
    return _enabled


def log(message):
    if not _enabled:
        return
    sys.stderr.write(f"[{_alias}] debug: {message}\n")
    sys.stderr.flush()


def usage_line(usage):
    """把 SSE 里返回的 usage 压成一行。字段缺失就省略，不做猜测。"""
    if not usage:
        return "usage: (服务端未返回)"
    parts = [f"prompt={usage.get('prompt_tokens', '?')}",
             f"completion={usage.get('completion_tokens', '?')}",
             f"total={usage.get('total_tokens', '?')}"]
    if "prompt_cache_hit_tokens" in usage:
        parts.append(f"cache_hit={usage.get('prompt_cache_hit_tokens')}")
    if "prompt_cache_miss_tokens" in usage:
        parts.append(f"cache_miss={usage.get('prompt_cache_miss_tokens')}")
    return "usage: " + " ".join(parts)
