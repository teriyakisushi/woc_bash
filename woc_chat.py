#!/usr/bin/env python3
"""woc_chat - 在终端中进行支持 Markdown 渲染的多轮 LLM 对话。"""

import os
import re
import sys

from woc import (
    C,
    LLMError,
    call_llm_messages_stream,
    clean_text,
    cerr,
    interactive_input,
    load_config,
)


CHAT_SYSTEM_PROMPT = """You are woc_chat, a helpful terminal assistant.
Answer the user's questions in their language. Use Markdown when it improves
readability. The current working directory is included as context. Do not
pretend that you can inspect files unless their contents are provided.
"""


def render_markdown(text: str, colors: C) -> str:
    """Render common Markdown constructs with ANSI colors, without dependencies."""
    text = clean_text(text)
    rendered = []
    in_code = False
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if (
            index + 1 < len(lines)
            and "|" in line
            and re.match(r"^\s*\|?\s*:?-{3,}", lines[index + 1])
            and "|" in lines[index + 1]
        ):
            table = []
            while index < len(lines) and "|" in lines[index].strip():
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                    table.append(cells)
                index += 1
            if table:
                column_count = max(len(row) for row in table)
                table = [row + [""] * (column_count - len(row)) for row in table]
                widths = [max(len(row[col]) for row in table) for col in range(column_count)]
                rendered.append("+" + "+".join("-" * (width + 2) for width in widths) + "+")
                for row_index, row in enumerate(table):
                    rendered.append(
                        "| " + " | ".join(cell.ljust(widths[col]) for col, cell in enumerate(row)) + " |"
                    )
                    if row_index == 0:
                        rendered.append("+" + "+".join("-" * (width + 2) for width in widths) + "+")
                rendered.append("+" + "+".join("-" * (width + 2) for width in widths) + "+")
            continue
        if line.strip().startswith("```"):
            in_code = not in_code
            if not in_code:
                rendered.append("")
            continue
        if in_code:
            rendered.append(colors(line, C.CYAN))
            continue
        line = re.sub(
            r"^(#{1,6})\s+(.+)$",
            lambda match: colors(match.group(2), C.BOLD, C.BLUE),
            line,
        )
        line = re.sub(
            r"\*\*(.+?)\*\*",
            lambda match: colors(match.group(1), C.BOLD),
            line,
        )
        line = re.sub(
            r"`([^`]+)`",
            lambda match: colors(match.group(1), C.YELLOW),
            line,
        )
        line = re.sub(r"^\s*[-*]\s+", "• ", line)
        rendered.append(line)
        index += 1
    return "\n".join(rendered)


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

    cerr(f"[woc_chat] 已进入聊天，当前目录: {cwd}", C.GREEN, C.BOLD)
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
        # 独立换行，确保回复不会和用户刚输入的内容混在同一行。
        sys.stdout.write("\n" + colors("[assistant] ", C.GREEN, C.BOLD))
        sys.stdout.flush()
        try:
            call_llm_messages_stream(cfg=cfg, messages=messages, on_chunk=chunks.append)
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
