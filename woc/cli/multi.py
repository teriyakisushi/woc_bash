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
    (":q / exit / quit", "end the session"),
    (":more / v", "expand the full output of the last command"),
    (":last", "show the last command again"),
    (":steps", "step audit for this turn"),
    (":explain", "explain the last command and its output (one request, not added to history)"),
    (":again", "re-run the last command (still needs confirmation)"),
    (":search [query]", "search the web and answer from the results (leave blank to infer the query from the chat)"),
    (":help", "show these commands"),
)


def show_help(alias):
    eprint(f"[{alias}] In-session commands:", C.GREEN, C.BOLD)
    for command, desc in HELP_LINES:
        eprint(f"  {command:<16} {desc}", C.GRAY)




def show_sessions(alias, command_name):
    """列出最近保存的会话（`--sessions`）。"""
    items = session.list_sessions()
    if not items:
        eprint(f"[{alias}] no saved sessions yet ([session] persist saves one after each turn)", C.YELLOW)
        return
    eprint(f"[{alias}] saved sessions (most recent {len(items)}):", C.GREEN, C.BOLD)
    for item in items:
        eprint(f'  {item["saved_at"]}  {item["count"]:>3} msgs  {item["name"]}', C.GRAY)
        eprint(f"      {item['cwd']}", C.GRAY)
    eprint(f"[{alias}] restore: {command_name} --resume <session-name>   or   {command_name} --last-session", C.GREEN)


def wocc_main(argv):
    argv = list(argv)
    capture_flag = "--capture-sudo" in argv
    loop_flag = "--loop" in argv
    resume_flag = "--last-session" in argv
    list_flag = "--sessions" in argv
    resume_name = None
    if "--resume" in argv:
        index = argv.index("--resume")
        resume_name = argv[index + 1] if index + 1 < len(argv) else ""
    flags = {"--capture-sudo", "--loop", "--last-session",
             "--sessions", "--resume"}
    argv = [item for item in argv
            if item not in flags and item != resume_name]

    cfg = load_config()
    name = prefix(cfg, "wocc_prefix")
    alias = prefix(cfg, "alias")
    capture = capture_flag or capture_sudo(cfg)
    if list_flag:
        show_sessions(alias, name)
        sys.exit(0)
    if resume_name == "":
        eprint(f"[{alias}] --resume needs a session name; run {name} --sessions first", C.RED)
        sys.exit(2)
    settings = cfg.get("loop") or {}
    loop_enabled = loop_flag or bool(settings.get("enabled"))
    max_steps = max_steps_for(cfg, loop_enabled)
    auto_safe = bool(settings.get("auto_safe"))
    store = session_settings(cfg)

    if argv and argv[0] in ("-h", "--help"):
        from .help import multi_help       # 延迟导入：正常路径不加载帮助文案
        multi_help(name, cfg, alias)
        sys.exit(0)

    if not sys.stdin.isatty():
        # 非终端时 read_key() 会退化成 input()，管道里的任意一行都会变成“确认”。
        eprint(f"[{alias}] {name} needs a terminal: standard input is not a terminal, so commands cannot be confirmed one by one. Exiting.", C.RED)
        eprint(f'[{alias}] to ask about command output, use: <command> | {prefix(cfg, "woc_prefix")} "your question"', C.GREEN)
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

    if resume_flag or resume_name:
        restored = session.load(store["max_messages"], store["max_chars"],
                                name=resume_name)
        if restored:
            messages.append({"role": "system",
                             "content": session.context_note(restored)})
            messages.extend(restored["messages"])
            restore_last(restored.get("last"))
            eprint(f'[{alias}] restored the previous session: {len(restored["messages"])} messages ({restored.get("saved_at", "unknown time")})',
                   C.GREEN, C.BOLD)
        else:
            eprint(f"[{alias}] no session to restore (never saved, or incompatible format)", C.YELLOW)

    eprint(f"[{alias}] multi-turn session started; type :q to exit (:help for commands)", C.GREEN, C.BOLD)
    if loop_enabled:
        eprint(f'[{alias}] loop enabled: up to {max_steps} steps; read-only commands skip confirmation: {"yes" if auto_safe else "no"}', C.GREEN)

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
                    cerr("[user] ", C.MAGENTA, C.BOLD),
                    notice=lambda msg: eprint(
                        f"[{alias}] {msg}", C.YELLOW),
                ).strip()
            except (KeyboardInterrupt, EOFError):
                eprint(f"\n[{alias}] session ended", C.GREEN)
                break

        if not user_input:
            continue
        if user_input in (":q", "exit", "quit"):
            eprint(f"[{alias}] session ended", C.GREEN)
            break
        if user_input in (":help", ":h", "?"):
            show_help(alias)
            continue
        if user_input == ":again":
            rerun_last(messages, alias=alias, cfg=cfg, capture=capture)
            continue
        if user_input in (":more", "v"):
            if not expand_last_output():
                eprint(f"[{alias}] no output to expand", C.YELLOW)
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
                eprint(f"[{alias}] no command has been run yet", C.YELLOW)
            continue

        # 人工指定搜索内容：搜到结果就直接接着让模型回答，不再等用户补一句。
        # 和 `woc -c` 共用 woc/search.py 里的同一份实现。
        start = len(messages)
        sources = None
        words = user_input.split(maxsplit=1)
        if words and words[0] == ":search":
            arg = user_input[len(":search"):].strip().lower()
            if arg in ("on", "off"):
                # wocc 没有"模型自主搜索"，也就没有开关；明说一句，别去搜 "off"。
                eprint(f"[{alias}] wocc only supports manual :search; there is no autonomous-search switch", C.YELLOW)
                continue
            from ..search import search_command      # 延迟导入：默认路径不加载
            sources = search_command(user_input, messages, cfg, alias)
            if sources is None:
                continue
        else:
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
        if sources:
            from ..search import print_sources       # 延迟导入：默认路径不加载
            print_sources(alias, sources)

        # 每回合结束后落盘：崩溃/断线时最多丢一个回合。
        if store["persist"]:
            saved = session.save(messages, model=cfg.get("model"),
                                 max_messages=store["max_messages"],
                                 max_chars=store["max_chars"],
                                 last=export_last())
            if not saved and not save_warned:
                eprint(f"[{alias}] failed to save the session: {session.session_path()}", C.YELLOW)
                save_warned = True
