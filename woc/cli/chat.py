import os
import sys

from ..colors import C, cerr, interactive_input
from ..config import load_config
from ..llm import LLMError, call_llm_messages_stream
from ..markdown import render_markdown

CHAT_SYSTEM_PROMPT = """You are woc_chat, a helpful terminal assistant.
Answer the user's questions in their language. Use Markdown when it improves
readability. The current working directory is included as context. Do not
pretend that you can inspect files unless their contents are provided.
"""


def woc_chat_main(argv=None):
    argv = argv or []
    if argv and argv[0] in ("-h", "--help"):
        print("用法: woc -c [首轮问题]")
        print("进入支持 Markdown 渲染的多轮聊天。输入 :q / exit / quit 退出。")
        raise SystemExit(0)

    cfg = load_config()
    cwd = os.getcwd()
    messages = [
        {"role": "system", "content": CHAT_SYSTEM_PROMPT},
        {"role": "system", "content": f"Current working directory: {cwd}"},
    ]
    pending = " ".join(argv) if argv else None
    colors = C(sys.stdout)

    cerr("[woc_chat] 已进入聊天模式", C.GREEN, C.BOLD)
    cerr("[woc_chat] 输入 :q 退出", C.GREEN)
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
                cerr("\n[woc_chat] 会话结束", C.GREEN)
                return

        if not question:
            continue
        if question in (":q", "exit", "quit"):
            cerr("[woc_chat] 会话结束", C.GREEN)
            return

        messages.append({"role": "user", "content": question})
        chunks = []
        sys.stdout.write("\n" + colors("[assistant] ", C.GREEN, C.BOLD))
        sys.stdout.flush()
        try:
            call_llm_messages_stream(
                cfg=cfg, messages=messages, on_chunk=chunks.append
            )
        except LLMError as exc:
            messages.pop()
            cerr(f"[woc_chat] LLM 调用失败: {exc}", C.RED)
            continue

        answer = "".join(chunks)
        if not answer:
            messages.pop()
            cerr("[woc_chat] LLM 未返回内容", C.RED)
            continue
        print(render_markdown(answer, colors))
        print()
        messages.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    woc_chat_main(sys.argv[1:])
