import os
import sys

from ..colors import C, cerr, eprint, interactive_input
from ..config import load_config, prefix, spinner as spinner_enabled
from ..llm import LLMError, call_llm_messages_stream
from ..markdown import MarkdownStream
from ..sanitize import strip_dsml
from ..ui import Spinner

CHAT_SYSTEM_PROMPT = """You are a helpful assistant.
Answer the user's questions in their language. Use Markdown when it improves
readability. The current working directory is included as context. Do not
pretend that you can inspect files unless their contents are provided.
"""


def woc_chat_main(argv=None, name=None, via_woc=False):
    argv = argv or []
    cfg = load_config()
    name = name or prefix(cfg, "woc_chat_prefix")
    alias = prefix(cfg, "alias")
    if argv and argv[0] in ("-h", "--help"):
        if via_woc:
            print(f"用法: {name} -c [首轮问题]")
        else:
            print(f"用法: {name} [首轮问题]")
            print(f"也可以由 {prefix(cfg, 'woc_prefix')} -c 启动。")
        print("进入支持 Markdown 渲染的多轮聊天。输入 :q / exit / quit 退出。")
        raise SystemExit(0)

    one_shot = bool(argv) and not sys.stdin.isatty()
    if not sys.stdin.isatty() and not argv:
        # 非终端时交互输入会退化成 input()，管道内容会被当成下一轮问题。
        eprint(f"[{alias}] 聊天模式需要终端：当前标准输入不是终端，已退出。", C.RED)
        raise SystemExit(2)

    cwd = os.getcwd()
    messages = [
        {"role": "system", "content": CHAT_SYSTEM_PROMPT.replace("{name}", alias)},
        {"role": "system", "content": f"Current working directory: {cwd}"},
    ]
    pending = " ".join(argv) if argv else None
    colors = C(sys.stdout)

    eprint(f"[{alias}] 已进入聊天，当前目录: {cwd}", C.GREEN, C.BOLD)
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

        messages.append({"role": "user", "content": question})
        chunks = []
        state = {"started": False}

        def write(text):
            sys.stdout.write(text)
            sys.stdout.flush()

        # 边收边渲染：SSE 一到就显示，不再攒到整条回答收完。
        # 行在显示前先过 strip_dsml，控制标记不会半截漏到屏幕上。
        renderer = MarkdownStream(
            colors, lambda line: write(line + "\n"), sanitize=strip_dsml)
        spin = Spinner(f"[{alias}] Thinking", enabled=spinner_enabled(cfg))

        def on_chunk(text):
            if not state["started"]:
                state["started"] = True
                spin.stop()          # 先清掉 loading 行，再往 stdout 写正文
                write("\n" + colors("[assistant] ", C.GREEN, C.BOLD))
            chunks.append(text)
            renderer.feed(text)

        spin.start()
        try:
            call_llm_messages_stream(
                cfg=cfg, messages=messages, on_chunk=on_chunk
            )
        except LLMError as exc:
            spin.stop()
            if state["started"]:
                write("\n")         # 流到一半断了：先换行，别把错误提示接在正文后面
            messages.pop()
            eprint(f"[{alias}] LLM 调用失败: {exc}", C.RED)
            continue
        finally:
            spin.stop()

        renderer.finish()
        answer = strip_dsml("".join(chunks))
        if not answer:
            messages.pop()
            eprint(f"[{alias}] LLM 未返回内容", C.RED)
            continue
        print()
        messages.append({"role": "assistant", "content": answer})
        if one_shot:
            return


if __name__ == "__main__":
    woc_chat_main(sys.argv[1:])
