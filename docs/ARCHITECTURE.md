# 架构：不变量、边界与依赖方向

这份文档写给"要改 `woc` 但不想先读两万行"的人。目录结构看 `AGENTS.md` 的代码地图；
这里只讲**为什么这样分层**，以及**哪些东西一碰就会坏**。

> 一句话：`woc` 的复杂度不在功能，而在"一条命令从模型嘴里到 shell 手里"这条链上
> 有四个必须保持的边界（§3）和一段必须逐字节不变的前缀（§4）。

---

## 1. 七条不变量

改代码前先看这张表。违反任何一条，`tests/` 里都有对应用例先红。

| # | 不变量 | 由谁保证 | 破了会怎样 |
|---|---|---|---|
| 1 | 单轮不循环 | `woc/cli/single.py` 用 `os.execvp` 交出进程；agentic 循环只在 `wocc` | `woc` 变成"不可预测的多步执行器"，一次性命令的语义没了 |
| 2 | 模型输出是单行命令 | `woc/sanitize.py` 的 `sanitize()` 只取第一条有效行 | 多行输出被静默截断，用户看到的和实际执行的不一致 |
| 3 | 每条命令一次人工确认 | `woc/loop.py` 的 `_confirm()`；只有"safe + 纯单命令 + `auto_safe=true`"才免确认 | 边界从"人按回车"变成"模型的判断" |
| 4 | 稳定前缀逐字节不变 | `woc/context.py` 的 `messages_prefix()`（§4） | 前缀缓存每轮都 miss，延迟与账单一起涨 |
| 5 | 模板只是文本 | `<templates>` 里的命令与手输命令过同一套 `is_dangerous` / `risk_tier` | 模板变成绕过危险检查的后门 |
| 6 | 历史格式会传染 | assistant 历史里的 `#CHAT:` / `#STEP:` 标记不许剥（`docs/DESIGN-agentic-loop.md §8.1`） | 剥掉之后模型失去"上一轮是对话还是步骤"的信号，开始乱续步 |
| 7 | 搜索不在默认路径 | `woc/search.py` 只在真搜索时懒导入；结果只进 messages 末尾；来源行永远打印 | 启动变慢、缓存前缀被污染、回答没有可审计来源 |

零依赖（只用标准库）是第 0 条：它是"装得上"的前提，不是审美偏好。

---

## 2. 入口分发（`woc/__main__.py`）

先做两件事，再分流：`load_config()` 读配置；`prefix(cfg, ...)` 把日志别名与三个命令名
改写成配置里的值（**代码里不许硬编码 `woc`**，否则改名安装后文案自相矛盾）。

| 触发 | 去处 | 备注 |
|---|---|---|
| 程序名是 `wocc` | `woc.cli.multi.wocc_main` | 延迟导入：单轮路径不加载 loop / 多轮 |
| `-c` / `--chat` | `woc.cli.chat.woc_chat_main`（`via_woc=True`） | 聊天模式，Markdown 渲染 + SSE |
| `--completion bash\|zsh` | `woc/cli/completion.py` | 生成补全脚本，纯文本输出 |
| `--templates`（可带 `--query`） | `__main__._cmd_list_templates` | 模板索引 + 两层注入的自检视图 |
| `--check-templates` | `__main__._cmd_lint_templates` | 期望 0 error / 2 warning（GNU `find -printf`） |
| `--install-templates [ids]` | `__main__._cmd_install_templates` | 项目级 → 用户级 |
| 其它（含无参数） | `woc.cli.single.woc_main` | 单轮：生成 → 确认 → `execvp` |
| `-h` / `--help` | `woc/cli/help.py`（各入口自己处理） | 只在 help 分支懒导入，正常启动不加载 |

`woc -c` 与 `wocc` 共用 `woc/search.py` 的 `search_command()`：人工 `:search` 只有一份
实现，差别在"搜完之后谁来跑下一回合"。

---

## 3. 三道边界（从模型输出到 shell）

```
模型输出 ──► sanitize()  ──► is_dangerous()/risk_tier() ──► bash -n 预检 ──► 人工确认 ──► exec
             单行 + 剥残留      命令位置判定（结构化）        语法，不是安全边界      Enter/y
```

1. **`sanitize()`（`woc/sanitize.py`）**：取第一条有效行；剥掉 DSML / 工具调用语法；
   拒绝 `<exec_result>`、`</br>` 这类"模型在假装是 harness"的残留；`is_root_shell()`
   拦 root 身份的 shell。它管**形状**，不管危险。
2. **`is_dangerous()` / `risk_tier()`（`woc/gate.py` / `woc/loop.py:75`）**：管**语义**。
   `gate.py` 是结构化的命令位置判定 —— `_split_segments` 切段、`_extract_substitutions`
   看替换、`_redirect_targets` 看重定向、`_unwrap_sudo|env|timeout|nice|...` 剥包装器、
   `_rm_reason` / `_chmod_reason` 单点判据，递归 + **fail-closed** 回退到旧子串规则。
   `risk_tier()` 返回 `deny` / `confirm` / `safe`：`deny` 不执行，`confirm` 要人按回车，
   `safe` 在 `auto_safe=true` 时免确认。
3. **`bash -n`（`woc/exec.py`）**：只解析不执行。它挡脏字符与拼写错误，**不是安全边界**
   ——文档里明写这句话，是为了避免有人把它当第二道门。

`gate.py` 的判定是"命令位置"级别的：`printf 'reboot'`、`grep reboot`、`man reboot`
不再误报；`systemctl reboot`、`sudo reboot`、`bash -c 'reboot'`、`find -exec rm` 仍然拦。
已知缺口（不是回归，是取舍）：`ps auxe` 与 `cat /proc/self/environ` 会被判 `safe`，
`redact_secrets()` 的字段名单也漏 `SECRET_KEY=` / `AWS_SECRET_ACCESS_KEY=` ——
证据与修法在 `docs/COMPETITORS-2026-11.md` §5.3 / §6.1。

---

## 4. 稳定前缀（为什么必须逐字节不变）

`messages_prefix()`（`woc/context.py:659`）按固定顺序拼出四段 `system` 消息：

| 顺序 | 内容 | 变化频率 |
|---|---|---|
| 1 | `SYSTEM_PROMPT`（`woc/prompts.py`，双模态：[A] 单行命令 / [B] `#CHAT:`） | 版本级 |
| 2 | `MULTI_TURN_HINT`（+ `LOOP_HINT`，仅 `wocc --loop`） | 会话级 |
| 3 | `<templates>`（索引层恒定全量 + 正文层按查询检索） | 会话级 |
| 4 | `<environment>`（cwd、平台、几条环境事实；`render_environment()`） | 会话级 |

实测前缀规模（`cwd="."`，`alias=woc`）：单轮 **8,225 字符**；多轮 **8,876**；
多轮 + loop **10,268**。这一段是缓存命中的唯一依据，所以：

- 不许塞时间戳、git 状态、实时负载、随机 id —— 一次变化 = 整段重建。
- 用户消息、执行结果、搜索结果一律追加在**前缀之后**，不进系统段。
- 明知会重建的操作要说明代价：`:search off/on` 改 hint（重建一次）、
  `--install-templates`（`<templates>` 变了，重建一次）。这是有意的例外，不是疏忽。

---

## 5. loop 状态机（`woc/loop.py`）

只有 `wocc`（`--loop`）会用。`#STEP:` 是**粘性**标记：

```
本轮没出现过 #STEP:
  裸命令  ──执行──► 回合结束
  #STEP:  ──执行──► 回合继续（从此进入"粘性"）

出现过 #STEP: 之后
  裸命令 / #STEP:  ──执行──► 继续
  #CHAT: <回答>    ──► 回合结束
  #ERROR: <原因>   ──► 回合结束
```

限额（`_limits()` / `max_steps_for()`）：`max_steps` 默认 **5**，硬上限 **50**
（`MAX_STEPS_LIMIT`，防误配）；累计输出上限 `total_chars` **24,000**；`auto_safe`
默认 **false** —— 默认每步都要按回车。

免确认的唯一组合是：`auto_safe=true` **且** `risk_tier()=="safe"` **且**
`is_pure_simple_cmd()`（无 `| & ; $ \` > < ( )`）**且**首词在 `READ_ONLY`
（32 条：`ls/cat/head/tail/wc/stat/file/df/du/free/ps/uname/id/whoami/date/uptime/
which/type/readlink/realpath/pwd/echo/grep/cut/tr/uniq/basename/dirname/md5sum/
sha256sum/lsblk/tree`）。`find`（`-exec`）、`sort`（`-o` 写盘）、`awk`/`sed`（可执行
命令）、`xargs`、`tee` 被刻意踢出：少几个白名单，换 0 个 safe 误判。

输出折叠只折"成功命令的 stdout"（25 行：头 15 + 尾 8），stderr、失败与模型回答
都不折；`:more` 用 `_LAST` 重新展开，`:steps` 用 `_STEPS` 审计本回合每一步。

---

## 6. 延迟导入边界（启动开销就是功能）

`import woc` 是懒加载门面（`woc/__init__.py` 的 `_EXPORTS` 字典 + `__getattr__`），
所以下面这些"重"模块必须到用的时候才 import：

| 模块 | 谁在什么时候导入 | 实测成本（本机，Python 3.10） |
|---|---|---|
| `woc/cli/help.py` | 各入口的 `-h/--help` 分支 | 0.4 ms / 59 modules |
| `woc/search.py` | 真搜索时（`#SEARCH:` 或 `:search`） | 17.0 ms / 141 modules |
| `woc/cli/multi.py` | 程序名是 `wocc` 时 | 21.8 ms / 157 modules |
| `woc/pipe.py` | stdin 不是 tty 且 `[pipe] enabled` 时（`woc/cli/single.py:26`） | 18.8 ms / 150 modules |
| `woc/cli/single.py` | 默认路径 | 24.5 ms / 155 modules |

`import woc` 本身 0.1 ms / 66 modules；`woc --help` 30～39 ms（端到端，含解释器启动）。
`tests/test_help.py` 与 `tests/test_search.py` 的 `LazyImportTest` 是这两条边界的守门人：
少了懒加载、或把 search 拉进启动路径，它们会红。

---

## 7. 依赖方向

```
cli/*        ──►  任意 woc/* 模块            （入口可以在最上层）
loop.py      ──►  sanitize, gate, exec, llm, context, session
cli/chat.py  ──►  llm, context, markdown, search(懒), session
woc/*        ──✕── cli/*                     （下层不许反向依赖入口）
sanitize.py / gate.py  纯函数，不做 IO       （谁都能调，且必须好测）
exec.py      只执行，不做判定                  （判定在 gate，别把策略塞进执行层）
config / colors / debug   无上行依赖，最底层
```

三条推论：

1. **判定与执行分离**：`gate.py` 决定能不能跑，`exec.py` 只管怎么跑。判定的回归测试
   在 `tests/test_gate.py`（改判据必须同步），执行层不需要重复判据。
2. **一个命令只有一个执行路径**：`LLM 生成 → sanitize → is_dangerous → 用户确认`
   （`woc/context.py:21` 的注释把这句话钉住了）。模板、补全、历史回放都不许另开路径。
3. **配置是两处事实**：`woc/config.py` 的默认值与 `compile.py` 的 `CONFIG_TEMPLATE`
   必须一致，`tests/test_compile.py` 按集合与值双向比对 —— 少一个键、多一个键、
   值不同都会红。

---

## 8. 数据与落盘（写哪里、留多久）

原则：**项目目录只读，状态全在 XDG 下，密钥先脱敏再落盘。**

| 数据 | 位置 | 生命周期与限制 |
|---|---|---|
| 会话 | `$XDG_STATE_HOME/woc/sessions/last.json`（默认 `~/.local/state/woc/sessions/`） | 目录 0700 / 文件 0600；`MAX_MESSAGES=40`、`MAX_CHARS=60000`、单条 `12000`；带时间戳归档保留 `MAX_ARCHIVES=20` 份（`--sessions` 列出，`--resume NAME` 恢复） |
| 配置 | `$XDG_CONFIG_HOME/woc/config.toml`（默认 `~/.config/woc/`） | 0600；环境变量优先于文件；默认值另有一份在 `compile.py` 的 `CONFIG_TEMPLATE`，两处必须一致 |
| 模板 | 用户级 `$XDG_CONFIG_HOME/woc/templates`（`woc/context.py:680`）；项目级 `./.woc/templates`（`woc/context.py:691`，`woc/context.py:198` 收集） | 纯文本 Markdown；安装走 `--install-templates`（项目级）与 `--global-templates`（用户级） |
| 调试输出 | stderr | `WOC_DEBUG=1` 才有（`woc/debug.py`：usage、前缀规模、loop 每步）。stdout 只放答案与命令 |
| 执行结果 | 不落盘 | 命令输出只在内存里回灌下一轮；`:more` 从 `_LAST` 重新展开 |

落盘前统一过 `redact_secrets()`（`woc/exec.py:91`）。它按 `password|passwd|passphrase|
secret|token|api_key` 六个字段名匹配，**已知漏配**（`SECRET_KEY=`、`AWS_SECRET_ACCESS_KEY=`、
`BEGIN RSA PRIVATE KEY`）——所以 `auto_safe=true` 下"免确认读环境变量"这件事有真实风险，
见 §3 末尾与 `docs/COMPETITORS-2026-11.md` §5.3。

---

## 9. 测试地图（改哪里，先跑哪一组）

全部离线，`python3 -m unittest discover -s tests`（260 个用例，约 0.3 s，1 个 live skip）。
`WOC_LIVE_TEST=1` 才真调 API —— 会有费用，默认不跑。

| 文件 | 守住什么 |
|---|---|
| `tests/test_gate.py` | 危险判定：`is_dangerous` / `is_root_shell` 的命令位置语义、包装器、递归、fail-closed 回退 |
| `tests/test_loop.py` | `risk_tier` 三档、`#STEP:` 粘性、步数与字符上限、折叠与 `:more` |
| `tests/test_pipe.py` | 管道模式：`detect_format` 只认 diff/json、prompt 不预设日志、二进制拒绝、头尾截断 |
| `tests/test_search.py` | 搜索：`query_by_llm` 两种来源、`derive_query` 回退、来源行、字符预算、懒加载 |
| `tests/test_session.py` | 落盘：脱敏、限长、0600/0700、归档裁剪、恢复 |
| `tests/test_compile.py` | 安装器与配置模板：与 `woc/config.py` 双向比对（少键/多键/值不同都红） |
| `tests/test_help.py` | 三个入口的 `--help`：flag 齐全、`[prefix]` 生效、无 argv 不走管道 |
| `tests/test_cli_guard.py` | 非终端输入不得当确认：`woc` 走管道、`wocc`/`woc -c` 拒绝启动 |
| `tests/test_chat_stream.py` / `tests/test_markdown.py` | SSE 增量、流式渲染、代码块与表格 |
| `tests/test_context.py` | 模板两层注入、`<environment>`、`messages_prefix()` 顺序与稳定性 |
| `tests/test_budget.py` / `tests/test_thinking.py` / `tests/test_completion.py` / `tests/test_commands.py` / `tests/test_package.py` | 预算换算、thinking 开关、补全脚本、`#CHAT:` 命令解析、包门面导出一致性 |

提交前必须跑的两条（`AGENTS.md` 同款）：

```bash
python3 -m compileall -q woc tests compile.py
python3 -m unittest discover -s tests
```

---

## 10. 改哪里，先看哪里

| 想改的事 | 先读 | 必须同步 |
|---|---|---|
| 加一个 CLI flag | `woc/__main__.py`（分流顺序）、`woc/cli/help.py` | `tests/test_help.py`；`docs/COMPETITORS-2026-11.md` §5.1 的 flag 清单 |
| 改危险判定 | `woc/gate.py`（`is_dangerous` / `_split_segments` / `_unwrap_*`） | `tests/test_gate.py`；`woc/loop.py:35` 的 `READ_ONLY` 与 `woc/loop.py:44` 的 `_UNBOUNDED`；§3 的缺口清单 |
| 改"要不要确认" | `woc/loop.py:75 risk_tier`、`_limits`、`_confirm` | `tests/test_loop.py`；**不许**为了少按回车放宽白名单 |
| 改系统提示 / 前缀 | `woc/prompts.py`、`woc/context.py:659 messages_prefix` | `tests/test_context.py`；§4 的四段顺序与"逐字节不变"（一次改动 = 一次缓存重建） |
| 改会话格式 | `woc/session.py`（`prepare` / `save` / `load`） | `tests/test_session.py`；脱敏与 0600 不许降级 |
| 改搜索结果注入 | `woc/search.py`（`perform` / `render_results` / `search_command`） | `tests/test_search.py`；结果只进 messages 末尾、来源行必打印、`query_by_llm` 两种来源都要测 |
| 改管道模式 | `woc/pipe.py`、`woc/prompts.py` 的 `PIPE_SYSTEM_PROMPT` | `tests/test_pipe.py`；"stdin 是数据不是指令"这条不许松 |
| 加配置项 | `woc/config.py` 对应小节 | `compile.py` 的 `CONFIG_TEMPLATE`、`docs/CONFIG.md`；`tests/test_compile.py` 会比对两处 |
| 改输出渲染 | `woc/markdown.py`、`woc/colors.py`（**风险最高**） | `tests/test_markdown.py` / `tests/test_chat_stream.py`；别在流式路径上做整段缓冲 |
| 改安装器 | `compile.py` | `tests/test_compile.py`；`~/bin/lib/woc` 是拷贝，改完要重跑 `python3 compile.py` |

---

## 相关文档

- `README.md` —— 用户视角：怎么用、配置、模板、已知限制。
- `AGENTS.md` —— 给 AI agent 的硬约束与代码地图（本文件是它的"为什么"）。
- `docs/CONFIG.md` —— 每个配置键的含义、默认值与环境变量。
- `docs/DESIGN-agentic-loop.md` —— loop 的设计取舍与踩过的坑（尤其 §8.1 历史格式）。
- `docs/DESIGN-web-search.md` —— 搜索的成本模型、注入位置与未做项。
- `docs/COMPETITORS-2026-11.md` —— 同类产品横评：我们缺什么、哪些不该抄。
- `docs/HANDOFF-2026-11-03.md` —— 本轮改动与下一步。
