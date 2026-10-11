import os
import sys

from ..colors import C, cerr, cout, eprint, interactive_input
from ..config import load_config
from ..context import messages_prefix
from ..exec import format_exec_result, run_command_capture
from ..llm import LLMError, StreamMode, call_llm_messages
from ..prompts import MULTI_TURN_HINT, SYSTEM_PROMPT
from ..sanitize import ChatResponse, CommandGenError, clean_text, is_dangerous, sanitize

def wocc_main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print("用法: wocc [首轮自然语言描述]")
        print("进入交互式多轮会话，每轮命令的执行结果会自动反馈给 LLM。")
        print("输入 :q / exit / quit 退出。")
        print("提问（如“解释一下”“为什么...”）会自动进入 chat 模式，不会被执行。")
        sys.exit(0)

    cfg = load_config()

    # 会话级稳定前缀：system prompt + 多轮说明 + template 参考库 + 环境。
    # 只在开始时构造一次，因此每轮请求的前缀逐字节相同 —— 前缀缓存能持续命中。
    messages = messages_prefix(
        multi_turn=True,
        system_prompt=SYSTEM_PROMPT,
        multi_turn_hint=MULTI_TURN_HINT,
    )

    eprint("[woc] 已进入多轮会话，输入 :q 退出", C.GREEN, C.BOLD)

    pending_input = " ".join(argv) if argv else None

    while True:
        if pending_input is not None:
            user_input = pending_input
            pending_input = None
            eprint(f"[user] {user_input}", C.MAGENTA, C.BOLD)
        else:
            try:
                user_input = interactive_input(
                    cerr("[user] ", C.MAGENTA, C.BOLD)
                ).strip()
            except (KeyboardInterrupt, EOFError):
                eprint("\n[woc] 会话结束", C.GREEN)
                break

        if not user_input:
            continue
        if user_input in (":q", "exit", "quit"):
            eprint("[woc] 会话结束", C.GREEN)
            break

        messages.append({"role": "user", "content": user_input})

        state = {"mode": None}

        def on_mode(mode):
            state["mode"] = mode

        def on_chunk(text):
            if state["mode"] == StreamMode.COMMAND:
                sys.stdout.write(cout(text, C.CYAN, C.BOLD))
            else:
                sys.stdout.write(text)
            sys.stdout.flush()

        # 显式从新行开始，避免 stdout 缓冲或重定向时命令粘在用户输入后面。
        sys.stdout.write("\n" + cout("[assistant] ", C.GREEN, C.BOLD))
        sys.stdout.flush()
        try:
            raw = call_llm_messages(
                messages, cfg, on_chunk=on_chunk, on_mode=on_mode
            )
        except LLMError as e:
            eprint(f"[woc] LLM 调用失败：{e}", C.RED)
            eprint("[woc] 本轮已跳过，可换个说法重试，或 :q 退出", C.YELLOW)
            messages.pop()
            continue

        # 命令模式：命令已流完，补换行，再打印提示
        if state["mode"] == StreamMode.COMMAND:
            sys.stdout.write("\n")
            sys.stdout.flush()
            eprint("[woc] 按 Enter 执行，输入 s 跳过，Ctrl+C 取消", C.GREEN)
        elif state["mode"] == StreamMode.CHAT:
            sys.stdout.write("\n")
            sys.stdout.flush()

        try:
            cmd = sanitize(raw)
        except ChatResponse:
            messages.append({"role": "assistant", "content": raw})
            continue
        except CommandGenError as e:
            eprint(f"[woc] {e}", C.RED)
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": (
                    "<exec_result>\n"
                    "exit_code: 1\n"
                    "stdout:\n\n"
                    f"stderr:\n命令生成失败：{clean_text(str(e))}\n"
                    "</exec_result>"
                ),
            })
            continue

        messages.append({"role": "assistant", "content": cmd})

        if is_dangerous(cmd):
            eprint("[woc] ⚠️  检测到潜在危险命令，已拒绝：", C.YELLOW, C.BOLD)
            eprint(f"  {cmd}", C.YELLOW)
            messages.append({
                "role": "user",
                "content": (
                    "<exec_result>\n"
                    "exit_code: 126\n"
                    "stdout:\n\n"
                    "stderr:\n命令因安全策略被拒绝，请换一种安全写法。\n"
                    "</exec_result>"
                ),
            })
            continue

        try:
            ans = interactive_input(prompt="")

        except (KeyboardInterrupt, EOFError):
            eprint("\n[woc] 会话结束", C.GREEN)
            break

        if ans.strip().lower() == "s":
            eprint("[woc] 已跳过", C.YELLOW)
            messages.append({
                "role": "user",
                "content": (
                    "<exec_result>\n"
                    "exit_code: 130\n"
                    "stdout:\n\n"
                    "stderr:\n用户跳过了这条命令。\n"
                    "</exec_result>"
                ),
            })
            continue

        out, err, code = run_command_capture(cmd)

        if out:
            sys.stdout.write(out)
            if not out.endswith("\n"):
                sys.stdout.write("\n")
        if err:
            sys.stderr.write(err)
            if not err.endswith("\n"):
                sys.stderr.write("\n")

        if code != 0:
            eprint(f"[woc] exit_code={code}", C.RED)

        messages.append({
            "role": "user",
            "content": format_exec_result(out, err, code),
        })
