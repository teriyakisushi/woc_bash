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


def single_help(name, cfg):
    """单轮 `woc`（也包括无参数时的用法提示）。"""
    woc, wocc, chat = _three(cfg)
    chat_hint = f"同 {chat}" if chat != woc else "聊天模式"
    print(f"""用法:
  {woc} <自然语言描述>            单轮：生成一条命令，确认后 execvp 交给 bash
  {woc} -c [首轮问题]             聊天：多轮对话 + Markdown 渲染 + 模型自主搜索（{chat_hint}）
  <命令> | {woc} "你的问题"        管道：stdin 当数据，只回答、不执行
  {wocc} [--loop]                 多轮：执行结果回灌给模型，命令逐条确认

单轮选项:
  -h, --help                       显示本帮助
  -c, --chat                       进入聊天模式
  --completion bash|zsh            生成 Shell 补全脚本（source 后生效）

模板（两层注入：用户级覆盖项目级同名 id）:
  --templates [--query "问题"]      列出模板、来源，以及某个查询会选中哪些正文
  --check-templates                自检模板（应 0 error / 0 warning）
  --install-templates [ID...]      安装模板到用户级目录

配套命令（纯 bash 环境里读写文件，不进 loop）:
  woc-fs read|write|edit PATH      read 按行区间、write 整块写入、edit 精确替换

管道选项:
  --max-chars N                    送进模型的 stdin 截断阈值（默认取 [pipe] max_chars）

示例:
  {woc} 找出当前目录下最大的 3 个文件
  {woc} 解释一下 du -sh 的用法
  cat err.log | {woc} "总结报错原因"
  {wocc} --loop "把 tests 里失败的用例修好"

确认边界: 每条命令执行前都要在终端里确认一次；没有终端就没有确认可言
配置: ~/.config/woc/config.toml（环境变量优先）  文档: README.md · docs/CONFIG.md""")


def multi_help(name, cfg, alias):
    """`wocc --help`：多轮会话。会话内命令复用 multi.show_help。"""
    woc, _, chat = _three(cfg)
    print(f"""用法:
  {name} [选项] [首轮自然语言描述]

交互式多轮会话：每轮执行的输出会自动回灌给模型。
提问（"解释一下""为什么…"）走 chat 模式，不会被执行。

选项:
  -h, --help                显示本帮助
  --loop                    开启粘性续步：出现过 #STEP: 的回合，裸命令也继续（默认关闭）
  --capture-sudo            需要 sudo 密码时在 pty 里捕获输出并回灌给模型
  --last-session            恢复上一次会话
  --sessions                列出已保存的会话
  --resume NAME             恢复指定会话（名字来自 --sessions）

退出: :q / exit / quit   输出过长时用 :more 展开
非终端: 需要逐条确认，所以不启动；要拿命令输出当数据提问，用 <命令> | {woc} "问题"
相关: {chat} 聊天模式 · {woc} 单轮
配置: ~/.config/woc/config.toml""")
    sys.stdout.flush()                  # 先把自己的输出刷出去，免得 stdout/stderr 交错
    from .multi import show_help        # 复用同一份会话内命令表，避免两份漂移
    show_help(alias)


def chat_help(name, cfg, via_woc):
    """`woc -c --help` / `woc_chat --help`。"""
    stream = sys.stdout
    woc, _, _chat = _three(cfg)
    if via_woc:
        usage = f"{woc} -c [选项] [首轮问题]"
    else:
        usage = f"{name} [选项] [首轮问题]"
        print(f"也可以由 {woc} -c 启动。", file=stream)
    print(f"""用法:
  {usage}

支持 Markdown 渲染的多轮聊天；模型可以自主搜索，回答后必打印来源行。

选项:
  -h, --help            显示本帮助
  --search              强制开启模型自主搜索（默认已开启，覆盖 [search] enabled）
  --no-search           本次会话关闭模型自主搜索；:search 仍然可用

会话内命令:
  :q / exit / quit      退出
  :search [关键词]       搜一次网页，然后直接让模型根据结果回答
                        留空 = 按刚才的对话自动推断检索词（[search] query_by_llm）
  :search on / off      本次会话开关模型自主搜索
  :help                 显示会话内命令

非终端: 有首轮问题就当一次性问答，没有则退出（聊天需要终端）
配置: ~/.config/woc/config.toml""", file=stream)
