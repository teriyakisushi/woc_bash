import sys

from ..colors import C, cerr, eprint, interactive_input
from ..config import capture_sudo, load_config, ls_on_start, prefix
from ..context import messages_prefix
from ..llm import LLMError
from ..loop import SessionEnd, expand_last_output, last_command, run_turn
from ..prompts import LOOP_HINT, MULTI_TURN_HINT, render_system_prompt

MAX_STEPS_LIMIT = 8


def wocc_main(argv):
    argv = list(argv)
    capture_flag = "--capture-sudo" in argv
    loop_flag = "--loop" in argv
    argv = [item for item in argv if item not in ("--capture-sudo", "--loop")]

    cfg = load_config()
    name = prefix(cfg, "wocc_prefix")
    alias = prefix(cfg, "alias")
    capture = capture_flag or capture_sudo(cfg)
    settings = cfg.get("loop") or {}
    loop_enabled = loop_flag or bool(settings.get("enabled"))
    max_steps = min(max(int(settings.get("max_steps", 5)), 1), MAX_STEPS_LIMIT)
    auto_safe = bool(settings.get("auto_safe"))

    if argv and argv[0] in ("-h", "--help"):
        print(f"用法: {name} [--loop] [首轮自然语言描述]")
        print("进入交互式多轮会话，每轮命令的执行结果会自动反馈给 LLM。")
        print("输入 :q / exit / quit 退出。")
        print("提问（如“解释一下”“为什么...”）会自动进入 chat 模式，不会被执行。")
        print("  --loop          允许模型用 #STEP: 续步（默认关闭）")
        print("  --capture-sudo  需要 sudo 密码时捕获输出并反馈给模型")
        print("会话内命令: :q 退出 · :more / v 展开上一条命令的完整输出 · :last 看上一条命令")
        sys.exit(0)

    # 会话级稳定前缀：system prompt + 多轮说明（+loop 说明） + template 参考库 + 环境。
    # 只在开始时构造一次，因此每轮请求的前缀逐字节相同 —— 前缀缓存能持续命中。
    hint = MULTI_TURN_HINT + "\n" + LOOP_HINT if loop_enabled else MULTI_TURN_HINT
    messages = messages_prefix(
        multi_turn=True,
        system_prompt=render_system_prompt(alias),
        multi_turn_hint=hint,
        with_listing=ls_on_start(cfg),
    )

    eprint(f"[{alias}] 已进入多轮会话，输入 :q 退出", C.GREEN, C.BOLD)
    if loop_enabled:
        eprint(f"[{alias}] loop 已开启：最多 {max_steps} 步，"
               f"只读命令免确认: {'是' if auto_safe else '否'}", C.GREEN)

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
                eprint(f"\n[{alias}] 会话结束", C.GREEN)
                break

        if not user_input:
            continue
        if user_input in (":q", "exit", "quit"):
            eprint(f"[{alias}] 会话结束", C.GREEN)
            break
        if user_input in (":more", "v"):
            if not expand_last_output():
                eprint(f"[{alias}] 没有可展开的输出", C.YELLOW)
            continue
        if user_input == ":last":
            command = last_command()
            if command:
                eprint(f"[{alias}] {command}", C.CYAN)
            else:
                eprint(f"[{alias}] 还没有执行过命令", C.YELLOW)
            continue

        start = len(messages)
        messages.append({"role": "user", "content": user_input})
        try:
            run_turn(messages, cfg, alias=alias, capture=capture,
                     loop_enabled=loop_enabled)
        except SessionEnd:
            break
        except LLMError:
            # run_turn 已经打印过原因；回滚本回合追加的消息，保持上下文干净。
            del messages[start:]
            continue
