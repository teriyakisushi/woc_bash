import os
import sys

from ..colors import C, cout, eprint
from ..config import (load_config, ls_on_start, prefix,
                      spinner as spinner_enabled,
                      syntax_check as syntax_check_enabled)
from ..context import build_messages
from ..exec import needs_privilege, syntax_error
from ..llm import LLMError, StreamMode, call_llm_messages
from ..prompts import render_system_prompt
from ..sanitize import (ChatResponse, CommandGenError, is_dangerous,
                        is_root_shell, sanitize)
from ..ui import Spinner


def woc_main(argv):
    cfg = load_config()
    name = prefix(cfg, "woc_prefix")
    alias = prefix(cfg, "alias")
    with_listing = ls_on_start(cfg)

    if not argv or argv[0] in ("-h", "--help"):
        print(f"用法: {name} <自然语言描述>")
        print(f"示例: {name} 帮我抓一次实时资源的 snapshot 到 a.txt")
        print(f"     {name} 解释一下 du -sh 的用法")
        sys.exit(0)

    if not sys.stdin.isatty():
        # 没有终端就没有“一次人工确认”。旧行为是 input() 读走管道的第一行、
        # 把任意内容当成 Enter，等于绕过确认；这里直接拒绝。
        eprint(f"[{alias}] 当前标准输入不是终端，无法确认执行，已退出。", C.RED)
        eprint(f"[{alias}] 命令执行需要终端里的一次人工确认。", C.GREEN)
        sys.exit(2)

    user_input = " ".join(argv)

    state = {"mode": None, "started": False}
    spin = Spinner(f"[{alias}] Thinking", enabled=spinner_enabled(cfg))

    def on_mode(mode):
        state["mode"] = mode

    def on_chunk(text):
        if not state["started"]:
            state["started"] = True
            spin.stop()
            sys.stdout.write(cout("[assistant] ", C.GREEN, C.BOLD))
        if state["mode"] == StreamMode.COMMAND:
            sys.stdout.write(cout(text, C.CYAN, C.BOLD))
        else:
            sys.stdout.write(text)
        sys.stdout.flush()

    messages = build_messages(
        user_input,
        render_system_prompt(alias),
        with_listing=with_listing,
    )
    spin.start()
    try:
        raw = call_llm_messages(messages, cfg, on_chunk=on_chunk, on_mode=on_mode)
    except LLMError as e:
        spin.stop()
        eprint(f"[{alias}] {e}", C.RED)
        sys.exit(3)
    finally:
        spin.stop()

    if state["mode"] == StreamMode.COMMAND:
        sys.stdout.write("\n")
        sys.stdout.flush()
        eprint(f"[{alias}] 按 Enter 执行，Ctrl+C 取消", C.GREEN)
    elif state["mode"] == StreamMode.CHAT:
        sys.stdout.write("\n\n")
        sys.stdout.flush()

    try:
        cmd = sanitize(raw)
    except ChatResponse:
        sys.exit(0)
    except CommandGenError as e:
        eprint(f"[{alias}] {e}", C.RED)
        sys.exit(4)

    if is_root_shell(cmd):
        eprint(f"[{alias}] ⚠️  该命令会打开交互式 root shell，已拒绝：",
               C.YELLOW, C.BOLD)
        eprint(f"  {cmd}", C.YELLOW)
        eprint(f"[{alias}] 请改成具体的非交互命令（如 sudo docker ps）", C.GREEN)
        sys.exit(6)

    if is_dangerous(cmd):
        eprint(f"[{alias}] ⚠️  检测到潜在危险命令，已拒绝：", C.YELLOW, C.BOLD)
        eprint(f"  {cmd}", C.YELLOW)
        sys.exit(5)

    if syntax_check_enabled(cfg):
        error = syntax_error(cmd)
        if error:
            eprint(f"[{alias}] 命令语法错误，未执行：", C.YELLOW, C.BOLD)
            eprint(f"  {cmd[:200]}", C.YELLOW)
            detail = "\n  ".join(error.strip().splitlines()[-3:])[:500]
            eprint(f"  {detail}", C.GRAY)
            sys.exit(2)

    if needs_privilege(cmd):
        eprint(f"[{alias}] ⚠️  该命令将以 root 权限执行，sudo 会在此终端要求密码",
               C.YELLOW, C.BOLD)

    try:
        input()
    except (KeyboardInterrupt, EOFError):
        eprint(f"\n[{alias}] 已取消", C.YELLOW)
        sys.exit(130)

    os.execvp("bash", ["bash", "-c", cmd])


