# 使用与安全

`woc` 怎么用、边界在哪。项目介绍与安装见 [README.md](../README.md)。

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

### 文件读写（`woc-fs`）

`woc-fs` 是给模型（也给人）用的文件读写命令：一条命令一个动作，输出只有一行摘要
加数据，不把整个文件灌进对话。

```bash
woc-fs read PATH --lines 40:80                 # 带行号读一段，默认前 60 行
woc-fs read PATH --tail 20                     # 最后 20 行
woc-fs write PATH --content @/tmp/new.py --mkdir --backup
woc-fs edit PATH --old 'return None' --new 'return []' --backup
```

- `read` 拒绝二进制文件，超过 20000 字符自动截断并附一句提示。
- `write` 先写临时文件再 `os.replace`，保留原文件权限位；`--dry-run` 只报摘要不落盘。
- `edit` 只做字面量替换，`--old` 命中 0 处或 2 处以上都直接报错，并告诉你命中在哪几行。
- 内容参数支持 `@file`（从文件读）与 `-`（从 stdin 读），绕开引号地狱。
- 退出码 0 成功 / 1 失败 / 2 参数错误；失败时 stderr 只有一行 `error: …`。

它和普通命令一样过危险检查与人工确认：**不进只读白名单**，`auto_safe` 不会把它
免确认。模型该在什么时候用它，写在 `.woc/templates/file-ops.md` 里。

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
- `:search [关键词]` 搜一次网页，让模型立刻根据结果回答，并在回答后打一行来源；
  留空则按刚才的对话推断检索词；
- `:more` / `v` 展开输出，`:last` 重看上一条命令。

命令生成后：

- `Enter` / `y` —— 执行
- `n` —— 跳过（不执行，把"用户跳过"反馈给模型）
- `e` —— 编辑这条命令（改完回车执行，会重新过一遍危险检查）
- `Ctrl+C` —— 结束会话
- 其它键不会执行：只提示一次“只认 Enter/y · n · e”。
  方向键、功能键的首字节是 `ESC`，不会被误当成“执行”

命令宽过一屏时，确认前会按词折行、缩进重排一遍再等你按键；
`e` 编辑的是原样的一条命令（含 `;` 与管道）。

编辑器（输入或编辑命令时）:

- `Ctrl+A` / `Ctrl+E` 行首 / 行尾，`Ctrl+U` / `Ctrl+K` 删到行首 / 行尾，
  `Ctrl+W` 删前一个词，`Ctrl+L` 清屏；
- `↑` / `↓` 翻历史（本进程内 200 条，跨会话不落盘）；
- 长行横向滚动，两端用 `~` 标出还有内容；`Ctrl+G` 把完整输入打到上方；
- 多行粘贴只保留第一行，其余字节丢弃并提示一次 —— 否则残留在 tty 缓冲里的
  内容会在下一次确认时被当成按键。

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
它默认开启联网搜索：模型判断需要实时信息时会自己搜一次再回答；搜索过程不刷屏，
回答后面会跟一行来源。想关掉用 `woc -c --no-search` 或会话内 `:search off`；
想指定搜索内容用 `:search <关键词>`。详见上面的「网页搜索（`[search]`）」。

回答是**流式**的：SSE 增量一到就逐行渲染上屏，不用等整条回答收完；
表格要看到整块才知道列宽，所以会晚一点整块出现。

## 安全说明

LLM 输出不应被视为绝对可信。执行命令前请检查命令内容、路径和参数，
尤其是涉及删除、权限、网络请求或系统配置的操作。

项目内置的高风险命令拦截不是“整条命令子串匹配”，而是按 shell 语法看**命令位置**：

- 放行：`printf '... reboot ...' > file`、`grep reboot`、`man reboot`、
  `journalctl -u reboot.target`、`grep mkfs`；
- 拦截：`systemctl reboot`、`sudo reboot`、`bash -c 'reboot'`、`find -exec reboot`、
  `rm -rf /`、`mkfs`、`dd of=/dev/sd*`、`> /dev/sd*`、`chmod -R 777 /`。

"只读"不等于"无泄露"：`cat /proc/self/environ`、`ps e` / `ps auxe` 会把进程的环境变量
（token、密钥）原样打出来。这类写法即使在 `auto_safe=true` 下也要人工确认；回灌给模型
与落盘的内容另外过一遍 `redact_secrets()`（字段名带 `secret`/`token`/`password` 的值、
`Bearer` 凭据、PEM 私钥块都换成 `***`；裸 token 仍不覆盖）。

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
命令名会跟随 `[prefix]` 配置，`<woc_prefix>-fs` 也一起补全。

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
