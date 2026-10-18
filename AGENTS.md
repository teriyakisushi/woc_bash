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
3. **模型输出是单行命令**：`sanitize()` 只取第一条有效行，多行会被静默截断。
4. **人工确认是边界**：每条命令执行前必须有一次确认。loop 只把"只读单命令"在
   `auto_safe=true` 时免确认，其余仍然逐条确认。
5. **前缀缓存**：稳定段（SYSTEM_PROMPT / MULTI_TURN_HINT(+LOOP_HINT) / `<templates>` /
   `<environment>`）在一个会话内必须逐字节不变。不要往里面塞时间、git 状态、实时负载。
6. **模板只是文本**：`<templates>` 里的命令与用户手输的命令同权，必须同样过危险检查。
7. **历史格式会传染**：assistant 历史里的 `#CHAT:` 标记不能剥掉（`docs/DESIGN §8.1`）。

## 常用命令

```bash
python3 woc.py "查看当前目录下最大的 3 个文件"   # 单轮（会 execvp，别在测试里直接跑）
python3 wocc.py --loop                          # 多轮 + agentic loop
python3 woc.py --templates                      # 模板索引与两层注入
python3 woc.py --check-templates                # 模板自检（应 0 error / 2 warning）

python3 -m compileall -q woc tests              # 语法
python3 -m unittest discover -s tests           # 离线回归（~30 个用例，2ms）
WOC_LIVE_TEST=1 python3 -m unittest discover -s tests   # 真调 API，默认跳过，会产生费用
```

CI 在 `.github/workflows/test.yml`，Python 3.10 / 3.13 跑同样两项检查。

## 代码地图

| 文件 | 职责 |
|---|---|
| `woc/loop.py` | agentic loop：`risk_tier`、`#STEP:` 状态机、输出折叠、`:more` |
| `woc/cli/multi.py` | `wocc` 会话循环（用户输入、`:q` / `:more` / `:last`） |
| `woc/cli/single.py` | 单轮 `woc`（确认后 `os.execvp`） |
| `woc/cli/chat.py` | `woc -c` 聊天（Markdown 渲染） |
| `woc/context.py` | 模板两层注入、`<environment>`、目录快照、`messages_prefix()` |
| `woc/llm.py` | SSE 流式 + `StreamFilter`（`#CHAT:` / `#STEP:` 识别） |
| `woc/sanitize.py` | 命令清洗、DSML 剥离、危险/root-shell 判定 |
| `woc/exec.py` | 命令执行、sudo/pty 捕获、`format_exec_result` |
| `woc/config.py` | 分段配置 + 环境变量优先 |
| `woc/colors.py` | 自绘行编辑器 + 彩色输出（**改动风险最高**） |
| `compile.py` | 安装器（命令改名、全局模板、卸载） |

## 代码风格

- 文档与注释用中文，标识符用英文。
- 日志前缀统一走 `[prefix].alias`，**不要硬编码 `[woc]`**。
- 改安全逻辑（`is_dangerous` / `is_root_shell` / `risk_tier`）必须同步 `tests/test_loop.py`。
- 新增配置项放在 `woc/config.py` 对应小节，并支持环境变量覆盖。
- 不要为了少按一次回车放宽 `risk_tier` 的白名单。

## 提交约定

- 信息用 `feat:` / `fix:` / `docs:` / `test:` + 中文摘要，正文写"为什么"。
- 提交前必须：`compileall` + `unittest discover -s tests` 全绿。
- 临时脚本用 `_` 前缀，跑完删除；不要提交 `_*.py` / `_*.txt`。

## 安全提醒

- `is_dangerous()` 是**子串匹配**，会误报（`ls | grep mkfs` 也会被拦）。
- `is_root_shell()` 有刻意缺口：`sudo -u root bash` 抓不到。
- 需要 sudo 密码且无 ticket 时，`wocc` 前台执行且**不回灌输出**（防止 root 可读内容进 prompt）。
- 模型偶尔会把中文回答当成命令（漏 `#CHAT:`）；已有 exit 127 提示，不要静默吞掉。
