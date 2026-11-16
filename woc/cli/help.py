"""`--help` 与"无参数"时打印的帮助文案：三个入口各一份，集中在这里避免漂移。

只在真要打印帮助时才 import（正常启动路径不许加载它）。
文案里的命令名一律走 `prefix(cfg, ...)`，不要硬编码 `woc`。
"""

import sys

from ..config import prefix

__all__ = ["single_help", "multi_help", "chat_help"]


def _three(cfg):
    return (prefix(cfg, "woc_prefix"), prefix(cfg, "wocc_prefix"),
            prefix(cfg, "woc_chat_prefix"))


SINGLE_HELP = """Usage:
  {woc} <natural-language request>   single-shot: generate one command, confirm, execvp to bash
  {woc} -c [first question]          chat: multi-turn + Markdown + autonomous web search ({chat_hint})
  <command> | {woc} "your question"  pipe: treat stdin as data, answer only, never execute
  {wocc} [--loop]                    multi-turn: feed results back, confirm each command

Single-shot options:
  -h, --help                       show this help
  -c, --chat                       enter chat mode
  --completion bash|zsh            generate a shell completion script (source it to enable)

Templates (two layers: user-level overrides project-level with the same id):
  --templates [--query "question"]  list templates, sources, and which bodies a query selects
  --check-templates                lint templates (expect 0 errors / 0 warnings)
  --install-templates [ID...]      install templates into the user-level directory

Companion command (read/write files in a plain bash environment; not part of the loop):
  woc-fs read|write|edit PATH      read a line range, write a whole file, edit a literal match

Pipe options:
  --max-chars N                    max stdin chars sent to the model (default: [pipe] max_chars)

Examples:
  {woc} find the 3 largest files in this directory
  {woc} explain how du -sh works
  cat err.log | {woc} "summarize the errors"
  {wocc} --loop "fix the failing tests under tests/"

Confirmation boundary: every command is confirmed once in a terminal; without a terminal
there is no confirmation.
Config: ~/.config/woc/config.toml (environment variables take precedence)  Docs: README.md · docs/CONFIG.md"""


MULTI_HELP = """Usage:
  {name} [options] [first natural-language request]

Interactive multi-turn session: the output of each command is fed back to the model.
Questions ("explain...", "why...") use chat mode and are not executed.

Options:
  -h, --help                show this help
  --loop                    enable sticky continuation: once #STEP: appears, bare commands
                            continue the turn (off by default)
  --capture-sudo            capture output in a pty when sudo needs a password, and feed it back
  --last-session            restore the previous session
  --sessions                list saved sessions
  --resume NAME             restore a specific session (name from --sessions)

Exit: :q / exit / quit   use :more to expand long output
Non-terminal: refuses to start because commands need per-step confirmation; to ask about
command output, use <command> | {woc} "question"
Related: {chat} chat mode · {woc} single-shot
Config: ~/.config/woc/config.toml"""


CHAT_HELP = """Usage:
  {usage}

Multi-turn chat with Markdown rendering; the model may search the web and always prints a
source line after the answer.

Options:
  -h, --help            show this help
  --search              force autonomous web search on (already the default; overrides
                        [search] enabled)
  --no-search           disable autonomous web search for this session; :search still works

In-session commands:
  :q / exit / quit      exit
  :search [query]       search the web once, then answer directly from the results
                        leave blank to infer the query from the conversation
                        ([search] query_by_llm)
  :search on / off      toggle autonomous web search for this session
  :help                 show in-session commands

Non-terminal: with a first question it runs as a one-shot Q&A; without one it exits
(chat needs a terminal)
Config: ~/.config/woc/config.toml"""


def single_help(name, cfg):
    """单轮 `woc`（也包括无参数时的用法提示）。"""
    woc, wocc, chat = _three(cfg)
    chat_hint = (f"same as {chat}" if chat != woc
                 else "chat mode")
    template = SINGLE_HELP
    print(template.format(woc=woc, wocc=wocc, chat=chat,
                          chat_hint=chat_hint))


def multi_help(name, cfg, alias):
    """`wocc --help`：多轮会话。会话内命令复用 multi.show_help。"""
    woc, _, chat = _three(cfg)
    template = MULTI_HELP
    print(template.format(name=name, woc=woc, chat=chat))
    sys.stdout.flush()                  # 先把自己的输出刷出去，免得 stdout/stderr 交错
    from .multi import show_help        # 复用同一份会话内命令表，避免两份漂移
    show_help(alias)


def chat_help(name, cfg, via_woc):
    """`woc -c --help` / `woc_chat --help`。"""
    stream = sys.stdout
    woc, _, _chat = _three(cfg)
    if via_woc:
        usage = f"{woc} -c [options] [first question]"
    else:
        usage = f"{name} [options] [first question]"
        print(f"Can also be started with {woc} -c.", file=stream)
    template = CHAT_HELP
    print(template.format(usage=usage), file=stream)
