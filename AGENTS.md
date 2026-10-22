# AGENTS.md

给在本仓库里工作的 AI agent 的说明。人类请看 [README.md](README.md)；
设计取舍与踩过的坑见 `docs/DESIGN-agentic-loop.md`（`docs/HANDOFF-*.md` 是本地的，
已 gitignore）。

## 项目一句话

`woc` 把自然语言变成 shell 命令：`woc`（单轮）、`wocc`（多轮 + 可选 agentic loop）、
`woc -c` / `woc_chat`（聊天）。纯 Python 3.10+ 标准库，无第三方依赖。

## 硬约束（改代码前必读）

1. **零依赖**：只用标准库。不要引入 PyYAML/requests 之类。
2. **单轮不可循环**：`woc` 用 `os.execvp` 交出进程（`woc/cli/single.py`），loop 只做在 `wocc`。
3. **模型输出是单行命令**：`sanitize()` 只取第一条有效行，多行会被静默截断；
   工具调用/XML 残留（DSML、`<exec_result>`、`</br>`）会被拒绝，不会执行。
4. **人工确认是边界**：每条命令执行前必须有一次确认。loop 只把"只读单命令"在
   `auto_safe=true` 时免确认，其余仍然逐条确认。非终端输入没有确认可言：
   `woc` 走管道模式（只问答、不执行），`wocc` / `woc -c` 直接拒绝启动。
5. **前缀缓存**：稳定段（SYSTEM_PROMPT / MULTI_TURN_HINT(+LOOP_HINT) / `<templates>` /
   `<environment>`）在一个会话内必须逐字节不变。不要往里面塞时间、git 状态、实时负载。
6. **模板只是文本**：`<templates>` 里的命令与用户手输的命令同权，必须同样过危险检查。
7. **历史格式会传染**：assistant 历史里的 `#CHAT:` / `#STEP:` 标记不能剥掉
   （`docs/DESIGN §8.1`）。loop 开启后是“粘性续步”：出现过 `#STEP:` 的回合，
   裸命令也继续，只有 `#CHAT:` / `#ERROR:` 结束。
8. **搜索不进默认路径**：`woc/search.py` 只在 `woc -c --search` / `:search` 时延迟
   导入，默认启动路径不许加载它；搜索结果注入在 messages 末尾，不进稳定前缀。

## 常用命令

```bash
python3 woc.py "查看当前目录下最大的 3 个文件"   # 单轮（会 execvp，别在测试里直接跑）
python3 wocc.py --loop                          # 多轮 + agentic loop
python3 woc.py --templates                      # 模板索引与两层注入
python3 woc.py --check-templates                # 模板自检（应 0 error / 2 warning）
WOC_DEBUG=1 python3 woc.py "..."                # 观测 usage/缓存/前缀规模（stderr）

python3 -m compileall -q woc tests              # 语法
python3 -m unittest discover -s tests           # 离线回归（210+ 个用例，1 个 live skip，~600ms）
WOC_LIVE_TEST=1 python3 -m unittest discover -s tests   # 真调 API，默认跳过，会产生费用
```

CI 在 `.github/workflows/test.yml`，Python 3.10 / 3.13 跑同样两项检查。

## 代码地图

| 文件 | 职责 |
|---|---|
| `woc/loop.py` | agentic loop：`risk_tier`、`#STEP:` 粘性状态机、语法预检、输出折叠、`:more` |
| `woc/cli/multi.py` | `wocc` 会话循环（用户输入、`:q` / `:more` / `:last`） |
| `woc/cli/single.py` | 单轮 `woc`（确认后 `os.execvp`） |
| `woc/cli/chat.py` | `woc -c` 聊天（Markdown 渲染，SSE 增量逐行上屏） |
| `woc/markdown.py` | 终端 Markdown 渲染：`render_markdown()` 一次性、`MarkdownStream` 流式 |
| `woc/cli/completion.py` | `--completion bash\|zsh` 补全脚本生成 |
| `woc/context.py` | 模板两层注入、`<environment>`、目录快照、`messages_prefix()` |
| `woc/llm.py` | SSE 流式 + `StreamFilter`（`#CHAT:` / `#STEP:` 识别） |
| `woc/sanitize.py` | 命令清洗、DSML 剥离、工具调用守卫、root-shell 判定 |
| `woc/gate.py` | 结构化危险判定：命令位置、包装器、递归、保守回退 |
| `woc/exec.py` | 命令执行、`bash -n` 语法预检、sudo/pty 捕获、`format_exec_result` |
| `woc/ui.py` | `Spinner`：等待模型/执行命令的一行式 loading（仅 tty） |
| `woc/pipe.py` | 管道模式：stdin 当数据、只问答不执行；二进制拒绝、头尾截断 |
| `woc/search.py` | 网页搜索：Search API 调用、结果渲染与字符预算（仅 `woc -c`） |
| `woc/budget.py` | 预算换算：context / output / input 三者互相约束 |
| `woc/session.py` | `wocc` 会话落盘/恢复：只存对话、脱敏、限长、0600 |
| `woc/debug.py` | `WOC_DEBUG=1` 观测：usage、前缀、输入规模、loop 步（只写 stderr） |
| `woc/config.py` | 分段配置 + 环境变量优先 |
| `woc/colors.py` | 自绘行编辑器 + 彩色输出（**改动风险最高**） |
| `compile.py` | 安装器（命令改名、全局模板、卸载） |

## 代码风格

- 文档与注释用中文，标识符用英文。
- 日志前缀统一走 `[prefix].alias`，**不要硬编码 `[woc]`**。
- 改安全逻辑（`woc/gate.py` 的 `is_dangerous` / `is_root_shell` / `risk_tier`）必须同步
  `tests/test_gate.py` 的 `GateTest`。
- 新增配置项放在 `woc/config.py` 对应小节，并支持环境变量覆盖。
- 不要为了少按一次回车放宽 `risk_tier` 的白名单。

## 提交约定

- 信息用 `feat:` / `fix:` / `docs:` / `test:` + 中文摘要，正文写"为什么"。
- 提交前必须：`compileall` + `unittest discover -s tests` 全绿。
- 临时脚本用 `_` 前缀，跑完删除；不要提交 `_*.py` / `_*.txt`。

## 安全提醒

- `is_dangerous()` 现在是**命令位置**匹配（`woc/gate.py`）：`printf '... reboot ...'`、
  `grep reboot`、`man reboot` 不再误报；`systemctl reboot`、`sudo reboot`、
  `bash -c 'reboot'`、`find -exec reboot`、`rm -rf /` 仍然拦截。
- 它不是沙箱：解析失败、stdin 喂给 shell、或 `awk`/`sed`/`python` 等脚本宿主会回退
  旧子串规则（fail-closed）；动态拼接、脚本文件内容仍可能绕过。
- `is_root_shell()` 有刻意缺口：`sudo -u root bash` 抓不到。
- `bash -n` 语法预检只解析、不执行；它挡的是脏字符/拼写错误，不是安全边界。
- 需要 sudo 密码且无 ticket 时，`wocc` 前台执行且**不回灌输出**（防止 root 可读内容进 prompt）。
- 模型偶尔会把中文回答当成命令（漏 `#CHAT:`）；已有 exit 127 提示，不要静默吞掉。
- `wocc` 默认把会话写到 `$XDG_STATE_HOME/woc/sessions/last.json`（脱敏后，0600）；
  改这段必须同步 `tests/test_session.py`，并且不要往项目目录里写。
