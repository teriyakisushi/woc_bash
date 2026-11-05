# woc

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-0-success)](woc.py)
[![Tests](https://img.shields.io/badge/tests-332%20passing-brightgreen)](.github/workflows/test.yml)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

`woc` 是一个运行在终端里的自然语言命令助手。它可以把日常描述转换成
Shell 命令，也可以回答问题、执行命令并在多轮会话中继续参考执行结果。

> 想改代码？先看 **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**：七条不变量、三道边界、
> 稳定前缀为什么不能动、loop 状态机，以及"改哪里先看哪里"的速查表。
> English version: [README_EN.md](README_EN.md)

## 功能

- `woc`：单轮生成并执行 Shell 命令。
- `wocc`：多轮交互会话；每轮命令的输出会反馈给模型。
- `woc -c` / `woc_chat`：支持 Markdown 输出的多轮聊天模式。
- 命令生成后会先等待确认，按 `Enter` 才会执行。
- 对高风险命令进行拦截，例如删除根目录、格式化磁盘和关机等。
- 支持 Backspace 编辑输入内容；彩色 prompt 不会被 readline 当作可编辑文本。
- 等待模型/执行命令时显示一行 loading；仅在终端生效，可用 `[ui] spinner` 关闭。
- 管道模式：`dmesg | woc "分析一下"` 把标准输入当数据，只回答、不执行。
- `WOC_DEBUG=1` 可观测 usage、前缀缓存命中与每次请求的规模。
- 会话内可用 `:steps` 审计本回合步骤、`:explain` 解释上一条命令；
  `wocc --last-session` 能接回上一次会话。
- 聊天模式（`woc -c`）需要时自动联网搜索（`--no-search` 关闭）；`woc -c` 与
  `wocc` 里都可以用 `:search <关键词>` 人工指定搜索内容。

## 环境要求

- Python 3.10 或更高版本
- Bash
- 一个兼容 OpenAI Chat Completions API 的服务端点
- 对应服务的 API Key

项目只使用 Python 标准库，不需要额外安装第三方依赖。

## 安装

在项目根目录执行：

```bash
python3 compile.py --key sk-xxxx
```

安装器默认把命令放到 `~/bin`，并尝试将该目录加入当前 Shell 的配置文件。
安装时会询问日志别名和三个命令名（直接回车用默认值，见 [docs/CONFIG.md](docs/CONFIG.md) 的 `[prefix]`）。
同时会装一个文件读写命令 `<woc 命令名>-fs`（默认 `woc-fs`）：名字从 `woc_prefix`
派生，不额外占一个配置键。
也可以跳过 API Key 配置，稍后手动设置：

```bash
python3 compile.py --no-key
```

指定安装目录：

```bash
python3 compile.py --prefix ~/.local/bin --key sk-xxxx
```

安装器还会把完整的配置模板写进 `~/.config/woc/config.toml`：**只补缺失项**，
已有的键和值一律不动。升级安装器不会丢配置，新增的配置项（比如 `[search]`）
装一次就到位。

安装完成后，如果当前终端还找不到命令，执行安装器提示的 `source` 命令，
或重新打开一个终端。

## 快速上手

```bash
woc 查看当前目录下最大的三个文件      # 单轮：生成一条命令，确认后执行
dmesg | woc "最近有什么异常"          # 管道：把输出当数据，只回答不执行
wocc --loop                          # 多轮 + agentic loop（每步确认，可 :steps 审计）
woc -c "systemd 是什么"               # 聊天模式：Markdown 渲染、流式输出、可搜索
```

确认键是 `Enter` 执行 / `n` 跳过 / `e` 编辑，其它键不会执行任何东西；`:q` 退出多轮会话。

## 配置

所有配置键、默认值与环境变量见 [docs/CONFIG.md](docs/CONFIG.md)。最小配置只有一个环境变量：

```bash
export WOC_API_KEY=sk-xxxx
```

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/USAGE.md](docs/USAGE.md) | 管道模式、`woc-fs`、多轮会话、会话恢复、聊天、安全说明、sudo、补全、卸载 |
| [docs/TEMPLATES.md](docs/TEMPLATES.md) | 模板格式、两层注入、装到全局、前缀缓存约束 |
| [docs/CONFIG.md](docs/CONFIG.md) | 每个配置键的含义、默认值、环境变量覆盖 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 七条不变量、三道边界、稳定前缀、loop 状态机、"改哪里先看哪里" |
| [docs/DESIGN-agentic-loop.md](docs/DESIGN-agentic-loop.md) | loop 的设计取舍与踩过的坑 |
| [docs/DESIGN-web-search.md](docs/DESIGN-web-search.md) | 搜索的成本模型、注入位置与未做项 |

## 安全

每条命令执行前都要经过一次人工确认；危险命令按**命令位置**拦截，不是整行子串匹配，
`printf '... reboot ...'` 与 `grep reboot` 不会被误报。`--loop` 的 `auto_safe` 只放行只读单命令。
完整边界（含 `ps e` / `cat /proc/self/environ` 这类"只读但会泄密"的形式）见
[docs/USAGE.md](docs/USAGE.md#安全说明)。

## License

[MIT](LICENSE)
