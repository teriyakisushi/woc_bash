import os
import sys

from ..colors import C, cout, eprint
from ..config import load_config
from ..llm import LLMError, StreamMode, call_llm
from ..sanitize import ChatResponse, CommandGenError, is_dangerous, sanitize

def woc_main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print("用法: woc <自然语言描述>")
        print("示例: woc 帮我抓一次实时资源的 snapshot 到 a.txt")
        print("     woc 解释一下 du -sh 的用法")
        sys.exit(0)

    user_input = " ".join(argv)
    cfg = load_config()

    state = {"mode": None}

    def on_mode(mode):
        state["mode"] = mode

    def on_chunk(text):
        if state["mode"] == StreamMode.COMMAND:
            sys.stdout.write(cout(text, C.CYAN, C.BOLD))
        else:
            sys.stdout.write(text)
        sys.stdout.flush()

    sys.stdout.write(cout("[assistant] ", C.GREEN, C.BOLD))
    sys.stdout.flush()
    try:
        raw = call_llm(user_input, cfg, on_chunk=on_chunk, on_mode=on_mode)
    except LLMError as e:
        eprint(f"[woc] {e}", C.RED)
        sys.exit(3)

    if state["mode"] == StreamMode.COMMAND:
        sys.stdout.write("\n")
        sys.stdout.flush()
        eprint("[woc] 按 Enter 执行，Ctrl+C 取消", C.GREEN)
    elif state["mode"] == StreamMode.CHAT:
        sys.stdout.write("\n\n")
        sys.stdout.flush()

    try:
        cmd = sanitize(raw)
    except ChatResponse:
        sys.exit(0)
    except CommandGenError as e:
        eprint(f"[woc] {e}", C.RED)
        sys.exit(4)

    if is_dangerous(cmd):
        eprint("[woc] ⚠️  检测到潜在危险命令，已拒绝：", C.YELLOW, C.BOLD)
        eprint(f"  {cmd}", C.YELLOW)
        sys.exit(5)

    try:
        input()
    except (KeyboardInterrupt, EOFError):
        eprint("\n[woc] 已取消", C.YELLOW)
        sys.exit(130)

    os.execvp("bash", ["bash", "-c", cmd])


