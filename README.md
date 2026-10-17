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

### 环境变量

```bash
export WOC_API_KEY="sk-xxxx"
export WOC_API_BASE="https://api.deepseek.com"
export WOC_MODEL="deepseek-flash"
export WOC_TIMEOUT="30"
export WOC_CAPTURE_SUDO="1"   # 等价于 [sudo] capture_output = true
export WOC_CONTEXT_LS="1"     # 等价于 [context] ls_on_start = true
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
```

环境变量优先于配置文件。默认 API Base 是
`https://api.deepseek.com`，默认模型是 `deepseek-flash`。

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
- `wocc` 只在**启动时生成一次**，并且只放进**第一次请求**，后续多轮不再携带；
- `woc_chat` 不注入。

快照可能过期，注入文本里已明确要求模型不要硬编码文件名、需要准确结果时重新生成命令；
输出最多 200 行 / 4000 字符，超出会标注「已截断」。对应环境变量 `WOC_CONTEXT_LS=1`。

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
woc --check-templates                        # 自检：危险子串误报、多行截断、可移植性坑
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

相关度打分：`tags` 命中查询权重 10（中文按 2 字滑窗兜底，所以"看看日志"也能命中
tag `日志`），`id` 直接出现在查询里权重 20，其余按词命中正文元数据计 1 分。

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
也可以把首轮输入直接放在命令后：

```bash
wocc 帮我看看当前运行中的进程
```

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

项目内置了一组高风险命令拦截规则，但这不是完整的安全沙箱。请在拥有
重要数据的环境中谨慎使用，并在需要时先备份数据。

### sudo 与权限

- 单轮 `woc` 里 sudo 直接接管终端：密码由 sudo 自己提示和缓存
  （`timestamp_timeout`），woc 不接触、不保存密码。
- 多轮 `wocc` 先探测 `sudo -n`：已有有效 ticket 就照常捕获输出；否则在当前
  终端前台执行，输出不反馈给模型（root 能读到的内容不应因此进入 prompt）。
- 会打开交互式 root shell 的命令（`sudo -i`、`sudo -s`、`su`）一律拒绝。
- 需要连续执行 sudo 命令时，可在同一终端先执行 `sudo -v`。
- 环境注入只包含工作目录、发行版、uid/groups 和容器类型，**不注入任何环境变量**。

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

运行 Python 语法检查：

```bash
python3 -m compileall -q woc
```

