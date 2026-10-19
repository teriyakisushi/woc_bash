import sys

from .. import session
from ..colors import C, cerr, eprint, interactive_input
from ..config import (capture_sudo, load_config, ls_on_start, prefix,
                      session_settings)
from ..context import messages_prefix
from ..llm import LLMError
from ..loop import (SessionEnd, explain_last, expand_last_output,
                    export_last, last_command, max_steps_for,
                    rerun_last, restore_last, run_turn, show_steps)
from ..prompts import LOOP_HINT, MULTI_TURN_HINT, render_system_prompt

HELP_LINES = (
    (":q / exit / quit", "结束会话"),
    (":more / v", "展开上一条命令的完整输出"),
    (":last", "重看上一条命令"),
    (":steps", "本回合的步骤审计"),
    (":explain", "解释上一条命令与输出（只发一次请求，不进历史）"),
    (":again", "重跑上一条命令（仍需确认）"),
    (":help", "显示这段帮助"),
)


def show_help(alias):
    eprint(f"[{alias}] 会话内命令：", C.GREEN, C.BOLD)
    for command, desc in HELP_LINES:
        eprint(f"  {command:<16} {desc}", C.GRAY)




def wocc_main(argv):
    argv = list(argv)
    capture_flag = "--capture-sudo" in argv
    loop_flag = "--loop" in argv
    resume_flag = "--last-session" in argv
    argv = [item for item in argv
            if item not in ("--capture-sudo", "--loop", "--last-session")]

    cfg = load_config()
    name = prefix(cfg, "wocc_prefix")
    alias = prefix(cfg, "alias")
    capture = capture_flag or capture_sudo(cfg)
    settings = cfg.get("loop") or {}
    loop_enabled = loop_flag or bool(settings.get("enabled"))
    max_steps = max_steps_for(cfg, loop_enabled)
    auto_safe = bool(settings.get("auto_safe"))
    store = session_settings(cfg)

    if argv and argv[0] in ("-h", "--help"):
        print(f"用法: {name} [--loop] [首轮自然语言描述]")
        print("进入交互式多轮会话，每轮命令的执行结果会自动反馈给 LLM。")
        print("输入 :q / exit / quit 退出。")
        print("提问（如“解释一下”“为什么...”）会自动进入 chat 模式，不会被执行。")
        print("  --loop          开启粘性续步：用过 #STEP: 后，裸命令也继续（默认关闭）")
        print("  --capture-sudo  需要 sudo 密码时捕获输出并反馈给模型")
        print("  --last-session  恢复上一次 wocc 会话（[session] persist）")
        print("会话内命令: :q 退出 · :more / v 展开输出 · :last 看上一条命令")
        print("            :steps 本回合的步骤审计 · :explain 解释上一条命令与输出")
        sys.exit(0)

    if not sys.stdin.isatty():
        # 非终端时 read_key() 会退化成 input()，管道里的任意一行都会变成“确认”。
        eprint(f"[{alias}] {name} 需要终端：当前标准输入不是终端，"
               "无法逐条确认命令，已退出。", C.RED)
        eprint(f"[{alias}] 想把命令输出当数据提问，用: <命令> | "
               f"{prefix(cfg, 'woc_prefix')} \"你的问题\"", C.GREEN)
        sys.exit(2)

    # 会话级稳定前缀：system prompt + 多轮说明（+loop 说明） + template 参考库 + 环境。
    # 只在开始时构造一次，因此每轮请求的前缀逐字节相同 —— 前缀缓存能持续命中。
    hint = MULTI_TURN_HINT + "\n" + LOOP_HINT if loop_enabled else MULTI_TURN_HINT
    messages = messages_prefix(
        multi_turn=True,
        system_prompt=render_system_prompt(alias),
        multi_turn_hint=hint,
        with_listing=ls_on_start(cfg),
    )

    if resume_flag:
        restored = session.load(store["max_messages"], store["max_chars"])
        if restored:
            messages.append({"role": "system",
                             "content": session.context_note(restored)})
            messages.extend(restored["messages"])
            restore_last(restored.get("last"))
            eprint(f"[{alias}] 已恢复上次会话："
                   f"{len(restored['messages'])} 条消息"
                   f"（{restored.get('saved_at', '时间未知')}）",
                   C.GREEN, C.BOLD)
        else:
            eprint(f"[{alias}] 没有可恢复的会话"
                   "（没保存过，或格式不兼容）", C.YELLOW)

    eprint(f"[{alias}] 已进入多轮会话，输入 :q 退出", C.GREEN, C.BOLD)
    if loop_enabled:
        eprint(f"[{alias}] loop 已开启：最多 {max_steps} 步，"
               f"只读命令免确认: {'是' if auto_safe else '否'}", C.GREEN)

    pending_input = " ".join(argv) if argv else None
    save_warned = False

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
        if user_input in (":help", ":h", "?"):
            show_help(alias)
            continue
        if user_input == ":again":
            rerun_last(messages, alias=alias, cfg=cfg, capture=capture)
            continue
        if user_input in (":more", "v"):
            if not expand_last_output():
                eprint(f"[{alias}] 没有可展开的输出", C.YELLOW)
            continue
        if user_input == ":steps":
            show_steps(alias)
            continue
        if user_input == ":explain":
            explain_last(alias, cfg)
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

        # 每回合结束后落盘：崩溃/断线时最多丢一个回合。
        if store["persist"]:
            saved = session.save(messages, model=cfg.get("model"),
                                 max_messages=store["max_messages"],
                                 max_chars=store["max_chars"],
                                 last=export_last())
            if not saved and not save_warned:
                eprint(f"[{alias}] 会话保存失败：{session.session_path()}",
                       C.YELLOW)
                save_warned = True
