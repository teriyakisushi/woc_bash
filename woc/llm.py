import json
import re
import time
import urllib.error
import urllib.request

from . import budget, debug
from .config import load_config, thinking_effort, thinking_enabled
from .context import build_messages
from .prompts import SYSTEM_PROMPT

_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")

def clean_text(s: str) -> str:
    if not s:
        return s
    s = _SURROGATE_RE.sub("\ufffd", s)
    return s.encode("utf-8", "ignore").decode("utf-8", "ignore")


class LLMError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


# Streaming Filter
class StreamMode:
    UNDECIDED = 0
    CHAT = 1
    COMMAND = 2
    SEARCH = 3


class StreamFilter:
    CHAT_MARKER = "#CHAT:"
    STEP_MARKER = "#STEP:"
    SEARCH_MARKER = "#SEARCH:"
    # 标记 -> 流式显示模式；#STEP: 仍然是一条命令，所以用 COMMAND（青色）。
    # #SEARCH: 不是给终端看的：chat 会压掉这段输出、自己去搜，再问模型一轮。
    _MARKERS = ((CHAT_MARKER, StreamMode.CHAT),
                (STEP_MARKER, StreamMode.COMMAND),
                (SEARCH_MARKER, StreamMode.SEARCH))

    def __init__(self):
        self.buf = ""
        self.mode = StreamMode.UNDECIDED
        self.acc = ""
        self.emitted = ""

    def _classify(self, stripped):
        """None=不是标记，("wait", None)=还可能是标记前缀，(marker, mode)=命中。"""
        for marker, mode in self._MARKERS:
            if stripped.startswith(marker):
                return marker, mode
        for marker, _mode in self._MARKERS:
            if marker.startswith(stripped):
                return "wait", None
        return None, None

    def feed(self, chunk):
        if not chunk:
            return ""
        self.acc += chunk

        if self.mode == StreamMode.UNDECIDED:
            self.buf += chunk
            stripped = self.buf.lstrip()
            marker, mode = self._classify(stripped)

            if marker == "wait":
                return ""

            if marker is not None:
                rest = stripped[len(marker):]
                self.mode = mode
                self.emitted += rest
                self.buf = ""
                return rest

            self.mode = StreamMode.COMMAND
            out = self.buf
            self.buf = ""
            self.emitted += out
            return out

        self.emitted += chunk
        return chunk

    def finish(self):
        if self.mode == StreamMode.UNDECIDED:
            stripped = self.buf.lstrip()
            marker, mode = self._classify(stripped)
            if marker is not None and marker != "wait":
                rest = stripped[len(marker):]
                self.mode = mode
                self.emitted += rest
                self.buf = ""
                return rest
            self.mode = StreamMode.COMMAND
            out = self.buf
            self.buf = ""
            self.emitted += out
            return out
        return ""


# streaming call
def call_llm_messages_stream(messages, cfg, on_chunk=None, on_mode=None):
    if not cfg["api_key"]:
        raise LLMError("未配置 API Key（WOC_API_KEY 或 ~/.config/woc/config.toml）")

    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            m["content"] = clean_text(c)

    url = cfg["api_base"].rstrip("/") + "/chat/completions"
    if debug.enabled():
        total_chars = sum(len(str(m.get("content") or "")) for m in messages)
        debug.log("request: model=%s messages=%d chars=%d max_tokens=%d timeout=%ss"
                  % (cfg["model"], len(messages), total_chars,
                     budget.output_tokens(cfg), cfg["timeout"]))
        if thinking_enabled(cfg):
            debug.log("thinking: on effort=%s" % thinking_effort(cfg))
        else:
            debug.log("thinking: off")
        debug.log(budget.describe(cfg))
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0,
        "max_tokens": budget.output_tokens(cfg),
        "stream": True,
        # 只有 debug 时才要求返回 usage：不改变常规请求的形状。
        **({"stream_options": {"include_usage": True}}
           if debug.enabled() else {}),
        # 尽量不让模型把工具调用的控制标记写进正文；sanitize 里还有一层兜底。
        "stop": ["<｜｜DSML｜｜", "</｜｜DSML｜｜>", "<||DSML||", "</||DSML||>"],
    }
    # 思考模式：DeepSeek 服务端默认开启（effort=high），这里显式下发，
    # 行为不跟着服务端默认值跑；关闭时也要发，否则"默认关闭"会被默认值推翻。
    if thinking_enabled(cfg):
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = thinking_effort(cfg)
    else:
        payload["thinking"] = {"type": "disabled"}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
        method="POST",
    )

    flt = StreamFilter()
    reported_mode = None
    usage = None
    started_at = time.monotonic()
    first_at = None

    def _emit(text):
        if text and on_chunk:
            on_chunk(text)

    def _maybe_report_mode():
        nonlocal reported_mode
        if flt.mode != StreamMode.UNDECIDED and flt.mode != reported_mode:
            reported_mode = flt.mode
            if on_mode:
                on_mode(flt.mode)

    try:
        resp = urllib.request.urlopen(req, timeout=cfg["timeout"])
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise LLMError(f"HTTP {e.code}: {body}", code=e.code) from e
    except Exception as e:
        raise LLMError(f"请求失败: {e}") from e

    try:
        for raw_line in resp:
            line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue

            if obj.get("usage"):
                usage = obj["usage"]
            try:
                delta = obj["choices"][0].get("delta") or {}
            except (KeyError, IndexError, TypeError):
                continue
            piece = delta.get("content")
            if not piece:
                continue

            out = flt.feed(piece)
            if out and first_at is None:
                first_at = time.monotonic()
            # 先报 mode 再吐字：调用方要在第一段正文上屏前知道模式。
            # 顺带修好 #STEP: 首段没有按命令上色的问题（on_mode 晚于 on_chunk）。
            _maybe_report_mode()
            _emit(out)
    except Exception as e:
        raise LLMError(f"流式读取失败: {e}") from e
    finally:
        try:
            resp.close()
        except Exception:
            pass

    tail = flt.finish()
    _maybe_report_mode()
    _emit(tail)

    if debug.enabled():
        elapsed = time.monotonic() - started_at
        first = "%.2fs" % (first_at - started_at) if first_at else "-"
        debug.log("llm: %.2fs first_token=%s" % (elapsed, first))
        debug.log(debug.usage_line(usage))

    if not flt.acc.strip():
        raise LLMError("LLM 未返回内容")

    return flt.acc.strip()


def call_llm_messages(messages, cfg, on_chunk=None, on_mode=None):
    return call_llm_messages_stream(messages, cfg, on_chunk=on_chunk, on_mode=on_mode)


def call_llm(user_input, cfg, on_chunk=None, on_mode=None, system_prompt=None,
             cwd=None, with_templates=True, with_environment=True,
             with_listing=None):
    """单轮调用。默认自动注入 template 参考库与运行环境（见 woc.context）。"""
    if system_prompt is None:
        system_prompt = SYSTEM_PROMPT.replace(
            "{name}", cfg.get("prefix", {}).get("alias", "woc"))
    if with_listing is None:
        with_listing = bool((cfg.get("context") or {}).get("ls_on_start"))
    messages = build_messages(
        user_input,
        system_prompt,
        cwd=cwd,
        with_templates=with_templates,
        with_environment=with_environment,
        with_listing=with_listing,
    )
    return call_llm_messages(messages, cfg, on_chunk=on_chunk, on_mode=on_mode)
