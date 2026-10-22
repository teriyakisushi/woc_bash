import os
import sys

from ..colors import C, cerr, eprint, interactive_input
from ..config import (load_config, prefix, search_enabled, search_max_chars,
                      search_settings, spinner as spinner_enabled)
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
    """流式请求一轮并渲染，返回（完整文本，流式模式）。

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
        call_llm_messages_stream(
            cfg=cfg, messages=messages, on_chunk=on_chunk, on_mode=on_mode
        )
    finally:
        spin.stop()

    renderer.finish()
    return strip_dsml("".join(chunks)), state["mode"]


def _split_search(text):
    """把 `#SEARCH: query` 拆成（搜索词，标记后面的正文）。

    协议上标记只占第一行；模型偶尔会在后面补一句正文，这里留着，不静默丢。
    """
    stripped = (text or "").strip()
    if not stripped.startswith(StreamFilter.SEARCH_MARKER):
        return "", text
    rest = stripped[len(StreamFilter.SEARCH_MARKER):]
    first, _sep, tail = rest.partition("\n")
    return first.strip(), tail.strip()


def _search_block(query, cfg, alias):
    """执行一次搜索，返回（结果列表 or None，注入上下文的块，错误信息 or None）。

    延迟导入 `..search`：`woc/__main__.py` 会在**所有**命令的启动路径上 import
    这个模块，顶层 import 等于给 `woc --help` 白加一个模块（见 DESIGN §2）。
    """
    from ..search import SearchError, render_error, render_results, search

    spin = Spinner(f"[{alias}] Searching", enabled=spinner_enabled(cfg))
    spin.start()
    try:
        hits = search(query, cfg)
    except SearchError as exc:
        return None, render_error(query, str(exc)), str(exc)
    finally:
        spin.stop()

    block = render_results(query, hits, max_chars=search_max_chars(cfg))
    return hits, block, None


def _print_hits(alias, hits, limit=5):
    """把结果清单打到 stderr：回答走 stdout，来源和状态走 stderr。"""
    for index, hit in enumerate(hits[:limit], 1):
        eprint(f"[{alias}] [{index}] {hit['title'] or '(无标题)'}", C.CYAN)
        if hit["url"]:
            eprint(f"       {hit['url']}", C.GREEN)
    if len(hits) > limit:
        eprint(f"[{alias}] 其余 {len(hits) - limit} 条只进上下文", C.GREEN)


def _handle_search_command(question, cfg, alias, messages):
    """`:search <关键词>`：手动搜一次，把结果加进上下文，不额外调模型。"""
    query = question[len(SEARCH_COMMAND):].strip()
    if not query:
        eprint(f"[{alias}] 用法: {SEARCH_COMMAND} <关键词>", C.YELLOW)
        eprint(f"[{alias}] 会把搜索结果加入上下文，之后正常提问即可", C.GREEN)
        return

    hits, block, error = _search_block(query, cfg, alias)
    if hits is None:
        eprint(f"[{alias}] 搜索失败: {error}", C.RED)
        return
    if hits:
        eprint(f"[{alias}] 搜索: {query}", C.CYAN, C.BOLD)
        _print_hits(alias, hits)
    else:
        eprint(f"[{alias}] 没有搜到结果", C.YELLOW)
    messages.append({"role": "user", "content": block})
    eprint(f"[{alias}] 已加入上下文（{len(hits)} 条），继续提问即可", C.GREEN)


def woc_chat_main(argv=None, name=None, via_woc=False):
    argv = argv or []
    cfg = load_config()
    name = name or prefix(cfg, "woc_chat_prefix")
    alias = prefix(cfg, "alias")

    # --search 只认开头：它是开关，不是问题的一部分。
    args = list(argv)
    flag_search = False
    while args and args[0] == "--search":
        flag_search = True
        args.pop(0)
    search_on = flag_search or search_enabled(cfg)
    settings = search_settings(cfg)

    if args and args[0] in ("-h", "--help"):
        if via_woc:
            print(f"用法: {name} -c [--search] [首轮问题]")
        else:
            print(f"用法: {name} [--search] [首轮问题]")
            print(f"也可以由 {prefix(cfg, 'woc_prefix')} -c 启动。")
        print("进入支持 Markdown 渲染的多轮聊天。输入 :q / exit / quit 退出。")
        print("  --search          允许模型在需要时自行搜索网页")
        print(f"  {SEARCH_COMMAND} <关键词>   先搜索网页，把结果加入上下文再提问")
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
        eprint(f"[{alias}] 模型可按需搜索网页；也可以用 {SEARCH_COMMAND} <关键词> 指定",
               C.GREEN)
    else:
        eprint(f"[{alias}] 用 {SEARCH_COMMAND} <关键词> 可以先搜网页（模型自主搜索未开）",
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
                    cerr("[user] ", C.MAGENTA, C.BOLD)
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
            _handle_search_command(question, cfg, alias, messages)
            if one_shot:
                return
            continue

        messages.append({"role": "user", "content": question})
        turns = 0
        # 一个回合里模型可能要搜一次再答：搜索是"多一轮请求"，不是新交互概念。
        while True:
            can_search = search_on and turns < settings["max_searches"]
            status = {"started": False}
            try:
                answer, mode = _stream_once(
                    messages, cfg, alias, colors, can_search, status)
            except LLMError as exc:
                if status["started"]:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                messages.pop()
                eprint(f"[{alias}] LLM 调用失败: {exc}", C.RED)
                break

            if mode == StreamMode.SEARCH and can_search:
                query, _tail = _split_search(answer)
                hits, block, error = _search_block(query, cfg, alias)
                if hits is None:
                    eprint(f"[{alias}] 搜索失败，已让模型改用自有知识: {error}", C.RED)
                elif not hits:
                    eprint(f"[{alias}] 没有搜到结果，已告知模型", C.YELLOW)
                else:
                    eprint(f"[{alias}] 搜索: {query}", C.CYAN, C.BOLD)
                    _print_hits(alias, hits)
                # 标记留在历史里：和 #CHAT: / #STEP: 同一个理由——模型会模仿
                # 自己上一轮的格式，剥掉标记会让它下一轮越来越不愿意写。
                messages.append({
                    "role": "assistant",
                    "content": StreamFilter.SEARCH_MARKER + " " + query,
                })
                messages.append({"role": "user", "content": block})
                turns += 1
                continue

            if mode == StreamMode.SEARCH:
                # 不能搜（没开 / 次数用尽）：标记已经压掉，别把 query 当答案回显。
                query, _tail = _split_search(answer)
                messages.pop()
                if search_on:
                    eprint(f"[{alias}] 本回合搜索已达上限 "
                           f"{settings['max_searches']} 次（模型想搜: {query}）",
                           C.YELLOW)
                else:
                    eprint(f"[{alias}] 模型想搜索（{query}），但搜索未开启；"
                           f"加 --search 或直接用 {SEARCH_COMMAND} <关键词>",
                           C.YELLOW)
                break

            if not answer.strip():
                messages.pop()
                eprint(f"[{alias}] LLM 未返回内容", C.RED)
                break
            print()
            messages.append({"role": "assistant", "content": answer})
            break
        if one_shot:
            return


if __name__ == "__main__":
    woc_chat_main(sys.argv[1:])
