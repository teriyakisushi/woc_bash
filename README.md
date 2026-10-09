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
```

### 配置文件

也可以创建 `~/.config/woc/config.toml`：

```toml
api_key = "sk-xxxx"
api_base = "https://api.deepseek.com"
model = "deepseek-flash"
timeout = 30
```

环境变量优先于配置文件。默认 API Base 是
`https://api.deepseek.com`，默认模型是 `deepseek-flash`。

不要把包含 API Key 的配置文件提交到 Git 或分享给他人。

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

