# woc

`woc` 是一个运行在终端里的自然语言命令助手。它可以把日常描述转换成
Shell 命令，也可以回答问题、执行命令并在多轮会话中继续参考执行结果。

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
安装时会询问日志别名和三个命令名（直接回车用默认值，见下面的 `[prefix]`）。
也可以跳过 API Key 配置，稍后手动设置：

```bash
python3 compile.py --no-key
```

指定安装目录：

```bash
python3 compile.py --prefix ~/.local/bin --key sk-xxxx
```

安装完成后，如果当前终端还找不到命令，执行安装器提示的 `source` 命令，
或重新打开一个终端。

## 配置

每个键的默认值、环境变量与校验规则见 **[docs/CONFIG.md](docs/CONFIG.md)**；
下面是常用部分的速查。

### 环境变量

```bash
export WOC_API_KEY="sk-xxxx"
export WOC_API_BASE="https://api.deepseek.com"
export WOC_MODEL="deepseek-flash"
export WOC_TIMEOUT="30"
export WOC_MAX_OUTPUT_TOKENS="4096"   # 等价于 [budget] max_output_tokens
export WOC_CAPTURE_SUDO="1"   # 等价于 [sudo] capture_output = true
export WOC_CONTEXT_LS="1"     # 等价于 [context] ls_on_start = true
export WOC_LOOP="1"           # 等价于 [loop] enabled = true
export WOC_SYNTAX_CHECK="0"   # 等价于 [exec] syntax_check = false
export WOC_SPINNER="0"        # 等价于 [ui] spinner = false
export WOC_PIPE="0"           # 等价于 [pipe] enabled = false
export WOC_PIPE_MAX_CHARS="32000"   # 等价于 [pipe] max_chars
export WOC_MAX_INPUT_CHARS="32000"  # 等价于 [budget] max_input_chars
export WOC_CONTEXT_TOKENS="64000"   # 等价于 [budget] context_tokens
export WOC_FOLD_LINES="25"          # 等价于 [ui] fold_lines
export WOC_DEBUG="1"                # 等价于 [debug] enabled = true
export WOC_SESSION_PERSIST="0"      # 等价于 [session] persist = false
export WOC_SESSION_MAX_MESSAGES="40"
export WOC_SESSION_MAX_CHARS="60000"
```

### 配置文件

也可以创建 `~/.config/woc/config.toml`：

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

[pipe]
enabled = true
# max_chars = 32000        # 可选：默认跟随 budget.max_input_chars

[budget]
context_tokens = 64000     # 模型上下文窗口
max_output_tokens = 4096   # 单次回复上限
max_input_chars = 32000    # 单次输入（管道数据）上限

[debug]
enabled = false

[session]
persist = true
max_messages = 40
max_chars = 60000
```

环境变量优先于配置文件。默认 API Base 是
`https://api.deepseek.com`，默认模型是 `deepseek-flash`。

### 预算（`[budget]`）

三个数字一起决定"这一次请求能有多大"，分散配置很容易配出互相矛盾的值
（比如输入上限比模型窗口还大），所以统一在一处：

| 键 | 默认 | 作用 |
|---|---|---|
| `context_tokens` | 64000 | 模型上下文窗口 |
| `max_output_tokens` | 4096 | 单次回复上限（太小会截断命令/回答） |
| `max_input_chars` | 32000 | 单次输入（管道数据）上限，单位是字符 |

换算按保守的 2 字符/token：有效输入上限 = `min(max_input_chars, (context_tokens - max_output_tokens - system 段) × 2)`，
所以把 `max_input_chars` 调到很大也不会撞上下文。
旧的根级 `output_maxtokens` 和 `WOC_OUTPUT_MAXTOKENS` 仍然可用（等同于
`[budget] max_output_tokens`），新配置建议放到 `[budget]`。

### 调试与观测（`[debug]`）

`WOC_DEBUG=1`（或 `[debug] enabled = true`）会把观测信息打到 **stderr**，
stdout 不受影响，管道模式可以照常 `| less`：

```text
[woc] debug: pipe: max_chars=32000 reserved=1140 chars
[woc] debug: pipe: stdin=1842 chars truncated=False
[woc] debug: request: model=deepseek-flash messages=3 chars=2900 max_tokens=4096 timeout=30s
[woc] debug: budget: context=64000tok output=4096tok input<=32000chars effective_input<=32000chars
[woc] debug: templates: 4 total, strategy=full, bodies=4, index=420 chars, body=2810 chars
[woc] debug: step 2/5: tier=confirm cmd='du -sh /var/log'
[woc] debug: llm: 2.31s first_token=0.84s
[woc] debug: usage: prompt=3456 completion=128 total=3584 cache_hit=3200 cache_miss=256
```

`usage` 里的 `cache_hit` / `cache_miss` 就是服务端前缀缓存的命中量 ——
改 prompt 稳定段之后，这两个数字应该保持稳定（见 `docs/DESIGN-agentic-loop.md` §7）。

### 命令名与提示前缀（`[prefix]`）

`[prefix]` 用来把 `woc` / `wocc` / `woc_chat` 换成别的名字：

| 键 | 作用 |
|---|---|
| `alias` | 所有日志/提示的前缀，也是 System Prompt 里助手自称的名字 |
| `woc_prefix` | `woc` 的命令名（帮助/用法里显示的名字） |
| `wocc_prefix` | `wocc` 的命令名 |
| `woc_chat_prefix` | `woc_chat` 的命令名 |

`woc -c` 跟随 `woc_prefix`（写作 `woc_prefix -c`），不能单独改名。例如：

```toml
[prefix]
alias = "qwe"
woc_prefix = "qwe"
wocc_prefix = "qwee"
woc_chat_prefix = "qwe_chat"
```

之后所有提示都统一带 `[qwe]` 前缀（与具体是哪个命令无关），System Prompt 里
也是 `You are qwe, ...`；而帮助/用法会显示各自的名字，例如 `qwee --help`。
`compile.py` 安装时会依次询问这几个名字，直接回车保留当前值。

### sudo 输出捕获（`[sudo]`）

默认关闭。开启后，`wocc` 在「命令需要 sudo 密码、且没有缓存 ticket」时会用 pty
（`script`）运行命令：密码仍由你在当前终端输入，但命令输出会被捕获并作为
`<exec_result>` 反馈给模型。也可以只对本次会话生效：

```bash
wocc --capture-sudo
```

对应环境变量 `WOC_CAPTURE_SUDO=1`。注意 root 权限能读到的内容可能包含敏感信息，
开启前请自行判断；捕获文本会对 `password=`、`token=`、`api_key=` 这类字段脱敏，
密码本身由 sudo 关闭回显，不会进入输出。`woc` 单轮模式不涉及捕获（输出直接打印到终端）。

### 启动时注入目录列表（`[context]`）

默认关闭。开启后：

- `woc`（单轮）每次调用都会注入当前目录的 `ls -alF` 快照；
- `wocc` 只在**启动时生成一次**，随后作为会话前缀的一部分每轮复用（不会重复执行 `ls`）；
- `woc_chat` 不注入。

`<environment>` 里还会带上用户的交互 shell（`$SHELL`）；命令始终由 `woc` 用 bash
执行，zsh/fish 专有写法由模型负责转换。
快照可能过期，注入文本里已明确要求模型不要硬编码文件名、需要准确结果时重新生成命令；
输出最多 200 行 / 4000 字符，超出会标注「已截断」。对应环境变量 `WOC_CONTEXT_LS=1`。

### Agentic loop（`[loop]`）

默认关闭。开启后，`wocc` 允许模型在**一个回合内**多走几步：模型用 `#STEP: `
显式续步；一旦本回合出现过 `#STEP:`，就进入“粘性续步”模式 —— 后面的普通
命令也会继续执行，直到 `#CHAT:` 给出结论，或到达步数/预算上限。

```bash
wocc --loop        # 只对本次会话生效
```

```toml
[loop]
enabled = false        # 等价于 --loop / WOC_LOOP=1
max_steps = 5          # 单回合最多几步；配置优先，安全上限 50
total_chars = 24000    # 单回合累计输出预算
auto_safe = false      # 只读单命令是否免确认
```

- 从未用过 `#STEP:` 的普通命令仍然是单命令回合：执行完就结束，只发一次请求。
- 用过 `#STEP:` 后，普通命令不再结束回合；这样模型不会在排查中途因为漏写
  标记而停下，也不需要你再输入“继续”。只有 `#CHAT:` / `#ERROR:` 会结束。
- `#STEP:` 会保留在 assistant 历史里（和 `#CHAT:` 同理），避免模型退回裸命令格式。
- `auto_safe=false`（默认）时每一步仍要按 Enter；设为 `true` 只让
  `ls`、`cat`、`grep`、`wc` 这类**只读单命令**自动执行。带管道、重定向、`$(...)`、
  `;` 的命令一律仍需确认；`find`、`sort`、`awk`、`sed` 不在白名单里
  （分别可 `-exec`、`-o` 写盘、执行命令）。
- 每步回显 `[step i/N]`；到步数或累计输出上限会明确提示，不会静默停止。

### 命令语法预检（`[exec]`）

默认开启。显示命令、等待确认之前，先用 `bash -n -c` **只做语法解析、不执行**。
语法错误不会弹确认，也不会执行；`wocc --loop` 里会把错误回灌给模型并继续，
`woc` 单轮直接报错退出。模型偶尔漏出的 `</br>`、`</exec_result>` 这类脏字符
会在这里被挡住。对应环境变量 `WOC_SYNTAX_CHECK=0` 可以关闭。

```toml
[exec]
syntax_check = true
```

### 等待与执行提示（`[ui]`）

默认开启，只在 `stderr` 是终端时显示。等待模型时显示
`[woc] Thinking ⠋ 1.2s`；`wocc` 执行被捕获的命令时显示
`[woc] Step 2/10 - Running ⠋ 3.4s`。命令开始输出或执行结束会清掉这一行。
`woc` / `wocc` / `woc -c` 共用，管道/重定向时自动静默。

```toml
[ui]
spinner = true
fold_lines = 25   # 超过多少行折叠；0 表示不折叠
```

对应环境变量 `WOC_SPINNER=0`；临时关闭也可以用 `WOC_NO_SPINNER=1`。

不要把包含 API Key 的配置文件提交到 Git 或分享给他人。

## Template 参考库

模板是**写给模型看的参考文本**，用来把"你在这个环境里积累的命令"固定下来，
减少模型瞎猜和来回确认。它只是文本，注入给模型参考，**不会被直接执行**。

**模板不享有任何权限豁免**：由模板派生出的命令与其它命令完全同等对待，同样要经过
危险模式检查与人工确认。模板是你自己维护的笔记，不是可信来源 —— 从别处 clone 来的
仓库里的模板尤其如此。

### 放置位置

```
<项目>/.woc/templates/*.md      # 项目级
~/.config/woc/templates/*.md    # 用户级，同名 id 会覆盖项目级
```

命令生成类入口（`woc`、`wocc`）会自动读取并注入；`woc -c` 聊天模式不注入。

### 模板格式

```markdown
---
id: top-ip-from-log
desc: 按客户端 IP 统计访问量前 N 名
tags: [nginx, 日志, 统计, awk]
when: 用户问"访问量最高的 IP / 哪个 IP 请求最多"
---

# 场景
分析 Web 访问日志，找出请求量最高的客户端。

# 参考命令（作者标注，未经验证）
```bash
awk '{print $1}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head -n 10
```

# 约定
- combined 格式下 IP 固定在第 1 列。

# 不要这样做
- 不要用 `cut -d' ' -f1`：请求行本身含空格，某些变体下会切错列。
```

`id` 可省略，默认取文件名。`desc` / `tags` / `when` 用于后续检索，
建议都写上。正文会**原样**注入，所以「不要这样做」这类章节可以直接写。

### 管理命令

```bash
woc --templates                              # 列出模板、来源与两层注入策略
woc --templates --query "看看日志里哪个 IP 最多"   # 预览某个查询会选中哪些正文
woc --check-templates                        # 自检：危险命令误拦、多行截断、可移植性坑
```

### 两层注入：索引恒定，正文按预算

模板分两层注入，**两种预算互不挤占**：

| 层 | 内容 | 策略 |
|---|---|---|
| 索引层 | `id \| desc \| 适用 \| tags` | **恒定全量**，让模型知道"有什么" |
| 正文层 | 完整正文 | 按查询相关度 + 字符预算（`BODY_BUDGET`，默认 4000） |

关键点：**检索只决定正文给谁，永远不裁剪索引**。如果按查询裁掉索引，模型就不知道
未命中的模板存在了。正文预算装得下全部时策略为 `full`，行为与全量注入完全一致；
装不下时转 `ranked`。

相关度打分（`select_bodies`）：

- `tags` 命中权重 10，`id` 直接出现在查询里权重 20；
- 其余词项按**逆文档频率**降权：只在 1 个模板里出现的词权重 1.0，几乎每个
  模板都出现的词趋近 0（没有语料库，就用当前模板集当 corpus）；
- 同一个词命中 `when` 记 2.5、`desc` 记 1.5、正文记 1.0；
- ASCII 标签按**词边界**匹配（`log` 不会命中 `catalog`），中文标签保持子串
  语义（中文按 2 字滑窗兜底，所以"看看日志"也能命中 tag `日志`）；
- 正文预算装不下时，**分数为 0 的模板不注入正文**（纯噪音），索引仍然全量。

**所以 `tags` 和 `when` 不是装饰** —— 写得好不好直接决定模板会不会被选中。

### 装到全局（所有项目可用）

项目级模板只在当前仓库生效。想跨项目复用：

```bash
woc --install-templates              # 把当前项目的模板全部装到用户级
woc --install-templates git-recent-changes   # 只装指定的几个
python3 compile.py --global-templates [ID...]  # 等价入口，走安装器
python3 compile.py --list-templates            # 先看看有哪些
```

内容相同的模板会被跳过（显示"已是最新"），重复执行是安全的。用户级目录遵循
`XDG_CONFIG_HOME`，默认 `~/.config/woc/templates`。

### 注入顺序与缓存

注入的消息顺序固定为：

```
system: 通用规则 → 多轮说明 → <templates>(索引+正文) → <environment> → user: 你的问题
```

前四段在会话内逐字节不变，因此能持续命中服务端的前缀缓存
（`prompt_cache_hit_tokens`）。**不要把每次都会变的内容（时间、git 状态、
实时负载）写进模板**，那会让缓存永久失效。

### 环境变量

```bash
export WOC_TEMPLATES=0            # 完全关闭模板注入
export WOC_TEMPLATES_USER=0       # 只关闭用户级模板
export WOC_TEMPLATES_PROJECT=0    # 只关闭项目级模板
export XDG_CONFIG_HOME=...        # 改用户级模板目录的位置
```

## 使用

### 单轮命令

```bash
woc 查看当前目录下最大的 10 个文件
woc 查找最近一天修改过的 Python 文件
```

如果模型返回的是命令，终端会先显示命令并等待确认；按 `Enter` 执行，
按 `Ctrl+C` 取消。如果请求被识别为解释类问题，`woc` 会直接输出回答。

### 管道模式（把命令输出当数据）

```bash
dmesg | woc "最近有什么异常"
journalctl -p err -n 200 | woc "归纳一下根因"
git diff | woc "review 这段改动"
kubectl get pods | woc "哪些 pod 不正常"
cat err.log | woc              # 没给问题时走内置的“排查/总结”指令
git diff | woc --max-chars 8000 "这段改动有什么风险"   # 单次覆盖输入上限
```

标准输入不是终端时自动进入管道模式：把 stdin 包成 `<stdin>` 块交给模型，
**只回答、不执行任何命令**。这条路径里没有命令执行，所以"每条命令都要人工确认"
的边界没有被放宽 —— 非终端输入本来就没法确认。答案走 stdout、提示走 stderr，
可以继续 `| less` 或 `> out.md`。

- 不注入 `<templates>`：那是命令参考，对"分析日志"只是噪音；
- 输入上限跟随 `[budget] max_input_chars`（默认 32000 字符），可用 `[pipe] max_chars`
  或单次 `--max-chars N` 覆盖；任何覆盖都会被上下文预算夹住。超出时保留头 60% +
  尾 40%，并在给模型的内容里显式标注"已截断"；
- 自动识别 unified diff 和 JSON，在 `<stdin>` 上标 `format="diff"` / `format="json"`，
  帮模型少猜一层；识别不了就不标，不给错误先验；
- 含 NUL 字节的二进制输入直接拒绝；ANSI 颜色会被剥掉；
- 和 `grep` 一样读到 EOF 才发请求：`tail -f` 这类不会结束的命令需要 `Ctrl+C` 取消；
- 用 `[pipe] enabled = false` 关掉本模式后，非终端输入退回"无法确认执行"的拒绝。

```toml
[pipe]
enabled = true
# max_chars = 32000     # 可选：默认跟随 budget.max_input_chars
```

对应环境变量 `WOC_PIPE=0` / `WOC_PIPE_MAX_CHARS=...`。

### 多轮会话

```bash
wocc
```

示例：

```text
[user] 查看当前目录下最大的 10 个文件
[assistant] ...
[user] 只看 Python 文件
```

输入 `:q`、`exit` 或 `quit` 退出。命令执行结果会自动加入下一轮上下文。
会话内命令（输入 `:help` 可以随时查看）：

- `:steps` 打印本回合每一步的命令、退出码与耗时；
- `:explain` 把上一条命令与输出发给模型解释（只发一次请求，不执行任何东西，也不进历史）；
- `:again` 重跑上一条命令（仍然过危险检查与人工确认，结果写回历史）；
- `:more` / `v` 展开输出，`:last` 重看上一条命令。

命令生成后：

- `Enter` / `y` —— 执行
- `n` —— 跳过（不执行，把"用户跳过"反馈给模型）
- `e` —— 编辑这条命令（改完回车执行，会重新过一遍危险检查）
- `Ctrl+C` —— 结束会话

输出很长时只折叠显示前 15 + 后 8 行；`stderr` 和失败的命令永远完整显示。
输入 `:more`（或 `v`）展开上一条命令的完整输出，`:last` 重看上一条命令。
编辑器里按 `Ctrl+G` 可以把当前输入完整打到上方（长输入被横向滚动时很有用）。
也可以把首轮输入直接放在命令后：

```bash
wocc 帮我看看当前运行中的进程
```

需要模型连做多步（后一步依赖前一步的真实输出）时加 `--loop`：

```bash
wocc --loop
```

想接着上一次会话继续：

```bash
wocc --last-session
```

看所有保存过的会话、或恢复其中某一个：

```bash
wocc --sessions
wocc --resume session-20261019-121500
```

### 会话恢复（`--last-session`）

`wocc` 每个回合结束后会把对话写到 `$XDG_STATE_HOME/woc/sessions/last.json`
（默认 `~/.local/state/woc/sessions/last.json`，权限 0600），同时留一份
`session-<时间戳>.json` 归档（保留最近 20 个）。下次用 `wocc --last-session`
接回最近一次，或用 `wocc --sessions` 列表 + `wocc --resume <名字>` 挑一个。

- **只回放 user/assistant**：system prompt、模板、`<environment>` 每次按当前环境
  重建，所以换目录、改配置、更新模板之后仍然自洽；
- 载入时从最近的消息往回取，默认最多 40 条 / 60000 字符，并保证第一条是 user；
- 历史前面会插一条 `<session_context>`，明确告诉模型"这是旧会话，可能已过期，
  需要准确结果时重新执行命令"；
- 落盘前脱敏（`password=` / `token=` / `api_key=` 这类字段），单条消息上限 12000 字符；
- 默认开启；`[session] persist = false`（或 `WOC_SESSION_PERSIST=0`）关闭写入。
  关闭后 `--last-session` 仍能读旧文件，但不会再产生新文件。

```toml
[session]
persist = true
max_messages = 40
max_chars = 60000
```

它解决的主要是**崩溃恢复**（长会话被 Ctrl+C 或关掉终端），不是多会话管理：
只有一个 `last.json`，多开 `wocc` 时后保存的覆盖先保存的。

### 聊天模式

```bash
woc -c 什么是 systemd？
# 或
woc_chat 什么是 systemd？
```

聊天模式专注于问答，不执行生成的内容。

## 安全说明

LLM 输出不应被视为绝对可信。执行命令前请检查命令内容、路径和参数，
尤其是涉及删除、权限、网络请求或系统配置的操作。

项目内置的高风险命令拦截不是“整条命令子串匹配”，而是按 shell 语法看**命令位置**：

- 放行：`printf '... reboot ...' > file`、`grep reboot`、`man reboot`、
  `journalctl -u reboot.target`、`grep mkfs`；
- 拦截：`systemctl reboot`、`sudo reboot`、`bash -c 'reboot'`、`find -exec reboot`、
  `rm -rf /`、`mkfs`、`dd of=/dev/sd*`、`> /dev/sd*`、`chmod -R 777 /`。

`sh -c` / `eval` / `find -exec` / `xargs` 会递归检查；解析失败或 stdin 喂给 shell 时
回退到旧的子串规则（fail-closed）。但它仍**不是完整沙箱**：脚本文件内容、动态拼接、
编码混淆都可能绕过；`awk` / `sed` / `python` 等可执行代码的宿主命令只在检测到
执行构造（如 `system(`、`-c`）时保守回退。请在拥有重要数据的环境中谨慎使用，
并在需要时先备份数据。

`--loop` 会把"每条命令都要人工确认"的边界下移：默认每一步仍需确认，只有
`auto_safe=true` 时"只读单命令"才免确认；粘性续步只改变“回合何时结束”，
不改变“每条命令是否要确认”；步数、累计输出都有硬上限，越界会明确提示。

标准输入不是终端时：`woc` 进入管道模式（只回答、不执行）；`wocc` 与 `woc -c`
（没有首轮问题时）直接拒绝启动 —— 交互式确认需要终端，不能拿管道里的任意一行
冒充回车。

### sudo 与权限

- 单轮 `woc` 里 sudo 直接接管终端：密码由 sudo 自己提示和缓存
  （`timestamp_timeout`），woc 不接触、不保存密码。
- 多轮 `wocc` 先探测 `sudo -n`：已有有效 ticket 就照常捕获输出；否则在当前
  终端前台执行，输出不反馈给模型（root 能读到的内容不应因此进入 prompt）。
- 会打开交互式 root shell 的命令（`sudo -i`、`sudo -s`、`su`）一律拒绝。
- 需要连续执行 sudo 命令时，可在同一终端先执行 `sudo -v`。
- 环境注入只包含工作目录、发行版、uid/groups 和容器类型，**不注入任何环境变量**。

### Shell 补全

生成 bash / zsh 补全脚本（只补命令名与参数，不涉及执行语义）：

```bash
source <(woc --completion bash)    # 临时启用
source <(woc --completion zsh)
```

想长期生效，把这一行加进 `~/.bashrc` / `~/.zshrc`（或把输出重定向到补全目录）。
命令名会跟随 `[prefix]` 配置。

## 卸载

删除命令并移除 Shell 配置中的 PATH 初始化：

```bash
python3 compile.py --uninstall
```

如需同时删除 `~/.config/woc/config.toml`：

```bash
python3 compile.py --uninstall --purge-config
```

## 开发检查

```bash
python3 -m compileall -q woc           # 语法
python3 -m unittest discover -s tests  # 离线回归测试（loop / 门禁 / 语法 / 标记历史）
```

真实 API 的 `#CHAT:` 标记合规测试**默认跳过**，需要显式开启（会调用模型、产生费用）：

```bash
WOC_LIVE_TEST=1 python3 -m unittest discover -s tests
```

CI 在 GitHub Actions 上用 Python 3.10 / 3.13 跑同样的两项检查
（见 `.github/workflows/test.yml`）。

