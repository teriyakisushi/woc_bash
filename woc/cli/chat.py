import os
import sys

from ..colors import C, cerr, eprint, interactive_input
from ..config import (load_config, prefix, search_enabled, search_settings,
                      spinner as spinner_enabled)
from ..llm import LLMError, StreamFilter, StreamMode, call_llm_messages_stream
from ..markdown import MarkdownStream
from ..prompts import SEARCH_HINT
from ..sanitize import strip_dsml
from ..ui import Spinner

CHAT_SYSTEM_PROMPT = """You are a helpful assistant.
Answer the user's questions in their language. Use Markdown when it improves
readability. The current working directory is included as context. Do not
pretend that you can inspect files unless their contents are provided.
"""

# 人工指定搜索内容的会话内命令（例如 `:search DeepSeek V4 发布`）。
SEARCH_COMMAND = ":search"


def _stream_once(messages, cfg, alias, colors, can_search, status):
    """流式请求一轮并渲染，返回（上屏文本，流式模式，完整文本）。

    两个文本不一样，别混用：
    - 上屏文本 = on_chunk 收到的片段拼接，用于写历史/判断空回答；
    - 完整文本 = `call_llm_messages_stream()` 的返回值（即 StreamFilter.acc），
      **以标记开头**。标记本身会被过滤器吃掉、不会进 on_chunk —— 真实 SSE
      经常把 `#SEARCH:` 单独送一块，这时候拼接结果里根本没有标记，拿它解析
      搜索词就会得到空串。

    `#SEARCH:` 永远不当作正文本上屏：能不能搜由调用方决定，标记本身对用户
    没有意义。on_mode 必须先于 on_chunk 到达，否则标记后的第一段正文已经
    写进 stdout 了（见 woc/llm.py 里 _maybe_report_mode 的位置）。
    """
    state = {"suppress": False, "mode": None}
    chunks = []

    def write(text):
        sys.stdout.write(text)
        sys.stdout.flush()

    renderer = MarkdownStream(
        colors, lambda line: write(line + "\n"), sanitize=strip_dsml)
    spin = Spinner(f"[{alias}] Thinking", enabled=spinner_enabled(cfg))

    def on_mode(mode):
        state["mode"] = mode
        if mode == StreamMode.SEARCH:
            state["suppress"] = True
            spin.stop()

    def on_chunk(text):
        chunks.append(text)
        if state["suppress"]:
            return
        if not status["started"]:
            status["started"] = True
            spin.stop()          # 先清掉 loading 行，再往 stdout 写正文
            write("\n" + colors("[assistant] ", C.GREEN, C.BOLD))
        renderer.feed(text)

    spin.start()
    try:
        raw = call_llm_messages_stream(
            cfg=cfg, messages=messages, on_chunk=on_chunk, on_mode=on_mode
        )
    finally:
        spin.stop()

    renderer.finish()
    return strip_dsml("".join(chunks)), state["mode"], strip_dsml(raw)


def _split_search(text):
    """把 `#SEARCH: query` 拆成（搜索词，标记后面的正文）。

    搜索词取**第一个非空行**：模型偶尔会把 query 写到标记的下一行，那不该
    变成空搜索词。后面的正文留着，不静默丢。
    """
    stripped = (text or "").strip()
    if not stripped.startswith(StreamFilter.SEARCH_MARKER):
        return "", text
    rest = stripped[len(StreamFilter.SEARCH_MARKER):]
    lines = [line.strip() for line in rest.splitlines()]
    query = ""
    consumed = 0
    for index, line in enumerate(lines):
        if line:
            query = line
            consumed = index + 1
            break
    return query, "\n".join(lines[consumed:]).strip()


def _toggle_search(messages, alias, current, wanted):
    """会话内开关模型自主搜索：改的是 system prompt 里那段 hint。

    这一下改的是稳定前缀，本会话的前缀缓存会重建一次 —— 只影响这一次，
    换来的是"不想花钱/不想它联网"时不用重启进程。
    """
    if wanted == current:
        eprint(f"[{alias}] 模型自主搜索已经是{'开启' if current else '关闭'}状态",
               C.GREEN)
        return current

    content = messages[0]["content"]
    if wanted:
        if not content.endswith(SEARCH_HINT):
            messages[0]["content"] = content + SEARCH_HINT
        eprint(f"[{alias}] 已开启模型自主搜索（前缀缓存会重建一次）", C.GREEN)
    else:
        if content.endswith(SEARCH_HINT):
            messages[0]["content"] = content[:-len(SEARCH_HINT)]
        eprint(f"[{alias}] 已关闭模型自主搜索（{SEARCH_COMMAND} on 恢复；"
               f"{SEARCH_COMMAND} <关键词> 仍然可用）", C.GREEN)
    return wanted


def _answer(messages, cfg, alias, colors, search_on, settings, rollback=True,
            sources=None):
    """跑一轮回答；模型用 `#SEARCH:` 时最多续 `max_searches` 轮。

    messages 必须已经以 user 消息结尾。`rollback` 为真时，失败的回合会把最后
    那条 user 消息弹掉（保持历史干净）；`:search` 走的是 rollback=False ——
    已经花掉一次搜索，失败也把结果留在上下文里，用户再问一句就能用。
    """
    sources = list(sources or [])
    turns = 0
    while True:
        can_search = search_on and turns < settings["max_searches"]
        status = {"started": False}
        try:
            answer, mode, full = _stream_once(
                messages, cfg, alias, colors, can_search, status)
        except LLMError as exc:
            if status["started"]:
                sys.stdout.write("\n")
                sys.stdout.flush()
            if rollback:
                messages.pop()
            eprint(f"[{alias}] LLM 调用失败: {exc}", C.RED)
            return False

        if mode == StreamMode.SEARCH and can_search:
            from ..search import describe, perform   # 延迟导入：默认路径不加载
            query, _tail = _split_search(full)
            hits, block, error = perform(query, cfg, alias)
            describe(alias, hits)      # 只有 WOC_DEBUG=1 时才真的打印
            if hits is None:
                eprint(f"[{alias}] 搜索失败，已让模型改用自有知识: {error}", C.RED)
            # 成功/空结果都不刷屏：命中列表只在 WOC_DEBUG=1 时打印
            # （见 woc/search.py 的 describe）。用户看到的只有答案 + 一行来源。
            messages.append({
                "role": "assistant",
                "content": StreamFilter.SEARCH_MARKER + " " + query,
            })
            messages.append({"role": "user", "content": block})
            sources = hits or []
            turns += 1
            continue

        if mode == StreamMode.SEARCH:
            # 不能搜（关掉了 / 次数用尽）：标记已经压掉，别把 query 当答案回显。
            query, _tail = _split_search(full)
            if rollback:
                messages.pop()
            if search_on:
                eprint(f"[{alias}] 本回合搜索已达上限 "
                       f"{settings['max_searches']} 次（模型想搜: {query}）",
                       C.YELLOW)
            else:
                eprint(f"[{alias}] 模型想搜索（{query}），但本会话关掉了自主搜索；"
                       f"{SEARCH_COMMAND} on 打开，或直接用 {SEARCH_COMMAND} <关键词>",
                       C.YELLOW)
            return False

        if not answer.strip():
            if rollback:
                messages.pop()
            eprint(f"[{alias}] LLM 未返回内容", C.RED)
            return False

        print()
        messages.append({"role": "assistant", "content": answer})
        if sources:
            from ..search import print_sources       # 延迟导入：默认路径不加载
            print_sources(alias, sources)
        return True


def woc_chat_main(argv=None, name=None, via_woc=False):
    argv = argv or []
    cfg = load_config()
    name = name or prefix(cfg, "woc_chat_prefix")
    alias = prefix(cfg, "alias")

    # 搜索默认开启（模型自己判断要不要搜），只认开头的开关：
    # --no-search 关掉本会话的自主搜索；--search 强制打开（覆盖 [search] enabled）。
    args = list(argv)
    flag_search = None
    while args and args[0] in ("--search", "--no-search"):
        flag_search = args.pop(0) == "--search"
    search_on = flag_search if flag_search is not None else search_enabled(cfg)
    settings = search_settings(cfg)

    if args and args[0] in ("-h", "--help"):
        from .help import chat_help        # 延迟导入：正常路径不加载帮助文案
        chat_help(name, cfg, via_woc)
        raise SystemExit(0)

    one_shot = bool(args) and not sys.stdin.isatty()
    if not sys.stdin.isatty() and not args:
        # 非终端时交互输入会退化成 input()，管道内容会被当成下一轮问题。
        eprint(f"[{alias}] 聊天模式需要终端：当前标准输入不是终端，已退出。", C.RED)
        raise SystemExit(2)

    cwd = os.getcwd()
    system_prompt = CHAT_SYSTEM_PROMPT
    if search_on:
        system_prompt += SEARCH_HINT
    messages = [
        {"role": "system", "content": system_prompt.replace("{name}", alias)},
        {"role": "system", "content": f"Current working directory: {cwd}"},
    ]
    pending = " ".join(args) if args else None
    colors = C(sys.stdout)

    eprint(f"[{alias}] 已进入聊天，当前目录: {cwd}", C.GREEN, C.BOLD)
    if search_on:
        eprint(f"[{alias}] 需要时会自动搜索网页（--no-search 关闭；"
               f"{SEARCH_COMMAND} off 也可关）", C.GREEN)
    else:
        eprint(f"[{alias}] 模型自主搜索已关闭；{SEARCH_COMMAND} <关键词> 仍可手动搜索",
               C.GREEN)
    eprint(f"[{alias}] 输入 :q 退出", C.GREEN)

    while True:
        if pending is not None:
            question = pending
            pending = None
            cerr(f"[user] {question}", C.MAGENTA, C.BOLD)
        else:
            try:
                question = interactive_input(
                    cerr("[user] ", C.MAGENTA, C.BOLD),
                    notice=lambda msg: eprint(
                        f"[{alias}] {msg}", C.YELLOW),
                ).strip()
            except (KeyboardInterrupt, EOFError):
                eprint(f"\n[{alias}] 会话结束", C.GREEN)
                return

        if not question:
            continue
        if question in (":q", "exit", "quit"):
            eprint(f"[{alias}] 会话结束", C.GREEN)
            return
        # 会话内命令按第一个词判断：:search / :search<空白>内容 都算，
        # 但 :searchfoo 不是（那是问题）。
        words = question.split(maxsplit=1)
        if words and words[0] == SEARCH_COMMAND:
            arg = question[len(SEARCH_COMMAND):].strip().lower()
            if arg in ("on", "off"):
                search_on = _toggle_search(messages, alias, search_on,
                                           arg == "on")
                if one_shot:
                    return
                continue
            from ..search import search_command     # 延迟导入：默认路径不加载
            # 搜到结果就直接让模型回答，不再要用户补一句"go ahead"。
            hits = search_command(question, messages, cfg, alias)
            if hits is not None:
                _answer(messages, cfg, alias, colors, search_on, settings,
                        rollback=False, sources=hits)
            if one_shot:
                return
            continue

        messages.append({"role": "user", "content": question})
        _answer(messages, cfg, alias, colors, search_on, settings)
        if one_shot:
            return


if __name__ == "__main__":
    woc_chat_main(sys.argv[1:])
