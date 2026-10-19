# woc 配置说明

配置文件默认在 `~/.config/woc/config.toml`（遵循 `XDG_CONFIG_HOME`）。
环境变量优先于配置文件；文件不存在时全部使用内置默认值。

配置文件是 TOML 的一个**很小子集**：只支持 `key = value`、`[section]` 和 `#` 注释，
不支持数组、多行字符串、嵌套表 —— 项目零依赖，没有引入 TOML 解析器。

## 优先级

```
环境变量  >  ~/.config/woc/config.toml  >  内置默认值
```

非法值不会让程序崩溃：会被忽略并在 stderr 打印一行
`[woc] 配置项 xxx 非法: ...，已忽略`，然后回落到默认值。

## 根级

| 键 | 默认 | 环境变量 | 说明 |
|---|---|---|---|
| `api_key` | 空 | `WOC_API_KEY` | API Key；空时报错退出 |
| `api_base` | `https://api.deepseek.com` | `WOC_API_BASE` | Chat Completions 端点前缀 |
| `model` | `deepseek-flash` | `WOC_MODEL` | 模型名 |
| `timeout` | `30` | `WOC_TIMEOUT` | 单次请求超时（秒） |
| `output_maxtokens` | `4096` | `WOC_OUTPUT_MAXTOKENS` | **旧键**，等同 `[budget] max_output_tokens` |

## `[prefix]`

日志/提示前缀与三个命令名。`compile.py` 安装时会依次询问；直接回车保留当前值。

| 键 | 默认 | 说明 |
|---|---|---|
| `alias` | `woc` | 所有日志前缀 `[alias]`，也是 System Prompt 里助手自称的名字 |
| `woc_prefix` | `woc` | 单轮命令名 |
| `wocc_prefix` | `wocc` | 多轮命令名 |
| `woc_chat_prefix` | `woc_chat` | 聊天命令名；`woc -c` 跟随 `woc_prefix`，不能单独改 |

命令名会变成可执行文件名，安装器只接受 `[A-Za-z0-9][A-Za-z0-9._-]*`。

## `[sudo]`

| 键 | 默认 | 环境变量 | 说明 |
|---|---|---|---|
| `capture_output` | `false` | `WOC_CAPTURE_SUDO` | 需要 sudo 密码且无 ticket 时用 pty 执行并捕获输出回灌给模型；默认前台执行、不回灌 |

## `[context]`

| 键 | 默认 | 环境变量 | 说明 |
|---|---|---|---|
| `ls_on_start` | `false` | `WOC_CONTEXT_LS` | 启动时注入一次 `ls -alF` 快照；`wocc` 只在启动时生成一次并复用 |

## `[loop]`（仅 `wocc`）

| 键 | 默认 | 环境变量 | 说明 |
|---|---|---|---|
| `enabled` | `false` | `WOC_LOOP` | 等价于 `wocc --loop` |
| `max_steps` | `5` | `WOC_LOOP_MAX_STEPS` | 单回合最多几步；硬上限 50 |
| `total_chars` | `24000` | `WOC_LOOP_TOTAL_CHARS` | 单回合累计回灌字符预算 |
| `auto_safe` | `false` | `WOC_LOOP_AUTO_SAFE` | 只读单命令是否免确认；管道/重定向/`$(...)` 一律仍需确认 |

## `[exec]`

| 键 | 默认 | 环境变量 | 说明 |
|---|---|---|---|
| `syntax_check` | `true` | `WOC_SYNTAX_CHECK` | 执行前用 `bash -n` 只做语法解析；挡脏字符，不是安全边界 |

## `[ui]`

| 键 | 默认 | 环境变量 | 说明 |
|---|---|---|---|
| `spinner` | `true` | `WOC_SPINNER` | 等待/执行时的一行 loading，仅 stderr 是 tty 时生效 |
| `fold_lines` | `25` | `WOC_FOLD_LINES` | 成功命令的 stdout 超过多少行折叠（头 15 + 尾 8）；`0` 表示不折叠 |

`WOC_NO_SPINNER=1` 是只对本次进程生效的临时开关（等价于关 spinner）。

## `[pipe]`

标准输入不是终端时，把 stdin 当数据问答（只回答、不执行）。

| 键 | 默认 | 环境变量 | 说明 |
|---|---|---|---|
| `enabled` | `true` | `WOC_PIPE` | 关掉后，非终端输入直接拒绝（不再进入管道模式） |
| `max_chars` | 跟随 `[budget] max_input_chars` | `WOC_PIPE_MAX_CHARS` | 单次送入的字符上限；仍会被上下文预算夹住 |

## `[budget]`

三个数字互相约束，避免出现"输入上限比模型窗口还大"。

| 键 | 默认 | 环境变量 | 说明 |
|---|---|---|---|
| `context_tokens` | `64000` | `WOC_CONTEXT_TOKENS` | 模型上下文窗口（token） |
| `max_output_tokens` | `4096` | `WOC_MAX_OUTPUT_TOKENS` | 单次回复上限（token）；调小省 token，但可能截断 |
| `max_input_chars` | `32000` | `WOC_MAX_INPUT_CHARS` | 单次输入上限（字符） |

有效输入上限：

```
min(max_input_chars, (context_tokens - max_output_tokens - system 段) × 2)
```

换算按保守的 2 字符/token；下限保底 1000 字符。

## `[debug]`

| 键 | 默认 | 环境变量 | 说明 |
|---|---|---|---|
| `enabled` | `false` | `WOC_DEBUG` | 把请求规模、预算、模板选择、usage（含前缀缓存命中）打到 **stderr** |

debug 输出不污染 stdout，管道模式仍可 `| less`。`usage` 只在 debug 时通过
`stream_options.include_usage` 请求，不改变常规请求形状。

## `[session]`

`wocc` 会话落盘，供 `--last-session` / `--sessions` / `--resume` 使用。

| 键 | 默认 | 环境变量 | 说明 |
|---|---|---|---|
| `persist` | `true` | `WOC_SESSION_PERSIST` | 每回合结束自动保存；关掉后不写新文件（仍能读旧文件） |
| `max_messages` | `40` | `WOC_SESSION_MAX_MESSAGES` | 保存/恢复的最大消息条数 |
| `max_chars` | `60000` | `WOC_SESSION_MAX_CHARS` | 保存/恢复的最大字符数 |

落盘位置：`$XDG_STATE_HOME/woc/sessions/`（默认 `~/.local/state/woc/sessions/`），
`last.json` 是最新会话，`session-YYYYmmdd-HHMMSS.json` 是归档（保留最近 20 个）。
文件权限 0600、目录 0700，保存前会脱敏 `password=` / `token=` / `api_key=` 这类字段。

## 只有环境变量的开关

| 环境变量 | 作用 |
|---|---|
| `WOC_TEMPLATES=0` | 关闭全部模板注入 |
| `WOC_TEMPLATES_USER=0` | 只关闭用户级模板（`~/.config/woc/templates`） |
| `WOC_TEMPLATES_PROJECT=0` | 只关闭项目级模板（`<项目>/.woc/templates`） |
| `XDG_CONFIG_HOME` | 改配置与用户级模板目录 |
| `XDG_STATE_HOME` | 改会话落盘目录 |
| `NO_COLOR` | 关闭颜色 |
| `WOC_FORCE_COLOR=1` | 强制颜色（调试重定向时用） |
| `WOC_NO_SPINNER=1` | 本次进程关闭 loading |

## 完整示例

```toml
api_key = "sk-xxxx"
api_base = "https://api.deepseek.com"
model = "deepseek-flash"
timeout = 30

[prefix]
alias = "woc"
woc_prefix = "woc"
wocc_prefix = "wocc"
woc_chat_prefix = "woc_chat"

[sudo]
capture_output = false

[context]
ls_on_start = false

[loop]
enabled = false
max_steps = 5
total_chars = 24000
auto_safe = false

[exec]
syntax_check = true

[ui]
spinner = true
fold_lines = 25

[pipe]
enabled = true
# max_chars = 32000      # 可选：默认跟随 budget.max_input_chars

[budget]
context_tokens = 64000
max_output_tokens = 4096
max_input_chars = 32000

[debug]
enabled = false

[session]
persist = true
max_messages = 40
max_chars = 60000
```

改完配置不需要重装；下一次启动即生效。模板与会话的改动会影响第一次请求的前缀，
前缀缓存会重建一次（见 `docs/DESIGN-agentic-loop.md` §10）。
