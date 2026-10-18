# 设计摘要：agentic loop 与 task 扩展

> 状态：**已实现（2026-10-18，基线 `fed74a8`）**。决策：`#STEP:` 协议、`--loop` 开启、
> `auto_safe=false`、本阶段不做 task。实现与测试结果见文末 §8。
>
> 目的不是说服，而是把**代价**摆出来：这个项目的卖点是"一条命令 + 一次确认 + 50ms 启动"，
> 任何让这三件事变贵的设计都该被质疑一次。

---

## 0. 六条结论

1. **loop 只做在 `wocc`**，单轮 `woc` 的 `execvp` 架构不动（沿用 §5.1 的结论）。
2. **协议用 `#STEP:` 显式续步，不用 `#DONE:` 强制收尾**。理由：普通回合**不发额外请求**，
   loop 的开关成本从 "+100% 请求" 降到 "0"（见 §2）。
3. 三档闸门 `risk_tier` 仍然要做，但它只决定**是否免人工确认**，不决定"要不要执行"。
4. 改动面 = **1 个新模块 + 5 个文件小改**，零依赖、零新命令；单轮 `woc` 的模块图可以做到**完全不变**。
5. 成本（估算）：普通回合 1 次请求（与今天相同）；3 步任务 3 次；前缀缓存让增量只按新输出计。
6. **task 建议先不做**。真要做，复用 template corpus（stage = 代码块里的行），+~120 行，
   不建新目录、不加新格式；等 loop 的 `execute_step` 抽稳了再叠。

---

## 1. 现状基线（本机实测，`fed74a8`）

| 指标 | 数值 | 说明 |
|---|---|---|
| `import woc` | **27.3 ms / 145 个模块** | 纯标准库 |
| `woc --help` 端到端 | **50 ms**（冷启动 71 ms） | 含配置读取 |
| 会话前缀 | **4 段 / 8707 字符** | 约 3.7k tokens |
| 前缀缓存 | **hit=3456 / miss≈240** | 第二次请求起稳定 |
| 单回合请求数 | **1** | 模型给命令 → 执行 → 回合结束 |
| 代码规模 | 2986 行（含 `compile.py` 601） | 无第三方依赖 |

这张表是后面所有"轻量化"判断的基准：**任何改动都应该能回答"它把哪一行变贵了"**。

---

## 2. 协议选择：`#DONE:` vs `#STEP:`

交接文档 §4.2 提的是 `#DONE:`：模型每轮要么给命令，要么 `#DONE: <最终回答>`。
它的问题不在实现，在**计费**：

- 模型给出一条命令后，harness 执行、回灌结果，然后**必须再问一次**"现在能收尾了吗"，
  模型才会输出 `#DONE:`。于是**每个回合至少 2 次请求**，哪怕只是 `ls` 一下。
- 按实测前缀 3.7k tokens 算，普通回合的输入成本直接翻倍。

`#STEP:` 反过来：**默认什么都不变**，模型只在"下一步真的依赖这一步输出"时，用
`#STEP: <命令>` 显式要求继续。

| 场景 | 今天 | `#DONE:` 方案 | `#STEP:` 方案（推荐） |
|---|---|---|---|
| 单命令回合 | 1 请求 | **2 请求** | **1 请求** |
| 2 步任务 | 答不完，要用户再问 | 3 请求 | 2 请求 |
| 5 步任务 | 不可能 | 6 请求 | 5 请求 |
| prompt 改动 | — | 新增"每轮必须 #DONE:"规则 | 新增"何时用 #STEP:"说明 |
| 终止判定 | 无 | 显式、无歧义 | 显式（模型给 `#CHAT:` 收尾） |
| 模型学错的表现 | — | 忘了 #DONE → 多跑一步 | 忘写 #STEP → 回合提前结束（**安全方向**） |

**推荐 `#STEP:`**，因为它把"要不要多跑一步"的默认值设成 **不跑**：
模型判断失误时，代价是"少跑一步、用户补一句"，而不是"多一次请求、多一条命令"。

反方向的失败（模型滥用 `#STEP:` 一直续步）由 `max_steps` 兜住。

> 兼容性：`#DONE:` 可以顺手当 `#CHAT:` 的别名接受（5 行），不写进 prompt，不影响成本。

---

## 3. 推荐设计

### 3.1 状态机

```
用户回合开始
  messages.append(user)
  for step in 1..max_steps:
      raw = call_llm_messages(messages, cfg)        # 流式，复用现有 SSE
      raw = strip_dsml(raw)

      if raw.startswith("#STEP:"):                  # 续步
          cmd = sanitize(raw[6:])
          tier = risk_tier(cmd)
          → 执行或拒绝（见 3.2）
          messages.append(assistant=cmd); messages.append(user=<exec_result>)
          budget -= len(result)
          continue

      if raw.startswith("#CHAT:") or raw.startswith("#DONE:"):
          print(answer); messages.append(assistant=answer); return   # 回合结束

      if raw.startswith("#ERROR:"):
          print(reason); return

      # 普通命令：执行完就结束本回合（与今天完全一致）
      cmd = sanitize(raw); 执行; messages.append(...); return
```

四条终止路径都要**显式打印原因**，不许静默停：

| 终止原因 | 输出 |
|---|---|
| 模型收尾 | `#CHAT:` 内容 |
| 步数到顶 | `[alias] 已达到 {max_steps} 步上限，停止自动执行` |
| 预算到顶 | `[alias] 累计输出预算用尽（{total} 字符），停止自动执行` |
| 连续重复命令 | `[alias] 模型连续两次给出同一条命令，已中止` |

### 3.2 `risk_tier`：三档，但只控制"要不要按回车"

```python
def risk_tier(cmd):
    if is_dangerous(cmd) or is_root_shell(cmd):
        return "deny"                     # 回灌 126，不执行、不计人工
    if not is_pure_simple_cmd(cmd):
        return "confirm"                  # 每条都要人工确认
    if cmd.split()[0] in READ_ONLY:
        return "safe"                     # 仅在 auto_safe=true 时免确认
    return "confirm"
```

`is_pure_simple_cmd`：命令里**不出现** `| & ; $ ` `` ` `` `> < ( ) \n` 任一字符。
这条线和交接文档 §4.3 一致，代价也照单全收：`find ... | sort | head` 会落进 `confirm`。

**白名单（保守版）**：

```
ls cat head tail wc stat file df du free ps uname id whoami date uptime
which type readlink realpath pwd echo grep cut tr uniq basename dirname
md5sum sha256sum lsblk tree
```

**刻意排除**（都能写盘或执行）：`awk`（`system()`）、`sed`（`e` 命令）、`sort -o`、
`find -exec/-delete`、`tee`、`xargs`、`env`、`watch`、`timeout`、`bash/sh`、`sudo`。

**两个必须写进测试的陷阱**（否则白名单等于形同虚设）：

1. `find . -exec rm {} +` —— **没有分号**，纯字符检查拦不住 → 必须对 `find` 额外检查
   参数里有没有 `-exec / -execdir / -delete / -ok`。
2. `sort -o /tmp/x` —— 带重定向语义的**写** → `sort` 必须检查 `-o`。

结论：与其做一个"看起来聪明"的判定，不如把 `find` / `sort` 从 `safe` 里**直接踢掉**，
只留真正只读的十几个命令。少 5 个白名单命令，换 0 个 `safe` 误判，划算。

### 3.3 预算与上限

沿用现有常量，不新增魔法数：

| 项 | 值 | 来源 |
|---|---|---|
| 单步输出上限 | 8000 字符 | `run_command_capture(max_output=8000)` 已存在 |
| 累计预算 | 24000 字符 | 新增，`3 × 单步` |
| 步数上限 | 5 | 新增，可配 1–8 |
| 单次请求超时 | 120 s | `run_command_capture(timeout=120)` 已存在 |

超预算时**不是静默截断**，而是：把本步结果截到剩余额度 → 打终止提示 → 结束回合。
`messages` 里保留已执行的部分，下一轮用户提问时模型仍能看见。

### 3.4 每步回显

```
[step 2/5] $ du -sk ./* | sort -rn | head -n1
  172K  ./woc
  exit_code=0
```

只是可审计性，**不是安全机制**（§4.5 的结论不变）。

### 3.5 sudo 步（新边界，必须定死）

需要密码、且没有 ticket 时，`wocc` 现在是"前台执行、输出不回灌"。
放进 loop 就是**失明的一步**：模型拿到空结果，会开始瞎猜。

推荐策略（`#STEP:` 场景下）：

1. 不执行该命令；
2. 回灌 `<exec_result> exit_code: 126 / 需要 sudo 密码但当前无法捕获输出`；
3. **计一步**，把决定权交回模型：改写成不需要 sudo 的命令，或 `#CHAT:` 告诉用户先 `sudo -v`。

替代方案（更严格）：直接中止 loop 并提示用户。差异在于"模型有没有机会自救"。

### 3.6 缓存

`LOOP_HINT` 属于 **stable 段**，在 `messages_prefix()` 里构造一次，loop 内不重建。
`#STEP:` 的指令文本固定，不引入任何"每回合变化"的内容（时间、步序号都不进 prompt）。

---

## 4. 改动面（轻量化核算）

### 4.1 文件清单

| 文件 | 变化 | 估算行数 |
|---|---|---|
| `woc/loop.py` | **新增**：`risk_tier` / `is_pure_simple_cmd` / 预算 / 状态机 | +170 |
| `woc/cli/multi.py` | 把 84–237 行的"单步"逻辑抽走，改为调用 `run_turn()` | −70 / +15 |
| `woc/llm.py` | `StreamFilter` 识别 `#STEP:`（否则会以命令颜色流式打印） | +10 |
| `woc/prompts.py` | `LOOP_HINT`（何时用 `#STEP:`、什么时候该收尾） | +12 |
| `woc/config.py` | `[loop] enabled / max_steps / total_chars / auto_safe` | +14 |
| `README.md` | 一节说明 + 配置示例 | +25 |
| **不动** | `context.py` `sanitize.py` `exec.py` `colors.py` `single.py` `chat.py` `compile.py` | 0 |

合计约 **+240 行**，净增 ~190 行。对比：现在全项目 2986 行（其中 `compile.py` 占 601）。

### 4.2 启动开销

- `woc`（单轮）**路径完全不变**：只要 `loop.py` 在 `wocc_main()` 内**延迟导入**，
  `woc` 的模块图、import 时间、行为都不受影响。
- `wocc` 多导一个模块：估算 **< 2 ms**（只有 `re` + 几个函数）。

### 4.3 使用开销

| 回合类型 | 请求数 | 新增 input tokens（估算） |
|---|---|---|
| 单命令（大多数） | 1（不变） | 0 |
| 2 步 | 2 | ≈ +300（第 1 步的命令与结果） |
| 5 步 | 5 | ≈ +1200 |

因为 messages 是**追加**的，第 N 步的整个前缀就是第 N−1 步的输入，前缀缓存能吃满，
**增量成本只按新追加的 output 计**，不是"每步重算全量"。

### 4.4 刻意不做

- ❌ 原生 function calling（破坏文本协议，见 §4.1）
- ❌ 多行脚本（与 `sanitize` 单行假设冲突，要另开 `#CMD:` 包裹块，另议）
- ❌ loop 状态落盘 / 断点续跑（`wocc` 是会话内进程，落盘带来版本与隐私问题）
- ❌ 并行步骤、子 agent、自动重试（每一步都要人看得懂，别搞成黑箱）
- ❌ 每步自动总结（等于每步多一次请求，收益低）

---

## 5. task 扩展

### 5.1 形态：复用 template corpus，不建新目录

一个 task = 一段**人工维护的、逐 stage 确认的**命令序列。最小实现是让它长得像模板：

````markdown
---
id: disk-full
desc: 磁盘占满时的排查流程
when: 用户说磁盘满 / No space left on device
tags: [disk, ops]
runnable: true
---
# 场景
根分区写满，需要先定位是谁占的。

# 步骤（每步都要人工确认）
```bash
df -h
du -sk /* 2>/dev/null | sort -rn | head -n 10
journalctl -p err -n 50 --no-pager
```
````

执行：`woc run disk-full`

```
[task disk-full 1/3] $ df -h
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda2        98G   93G  2.1G  98% /
按 Enter 执行 / s 跳过 / q 退出
```

### 5.2 执行语义（三条硬规矩）

1. **只有 `woc run <id>` 会执行**。模板注入、`wocc`、任何自动路径都不会跑 task。
2. **每个 stage 都要人工确认**，并且 stage 命令同样过 `is_dangerous` / `is_root_shell`。
3. **stage 之间不共享 shell 状态**（每步独立 `bash -c`，和 loop 一样）→ 只能写"一条命令出结果"的步骤，
   不能写 `cd`、不能用上一步的变量。这是硬约束，不是缺陷。

### 5.3 改动面

| 文件 | 变化 | 行数 |
|---|---|---|
| `woc/cli/task.py` | 新增：跑 stage、确认、危险拦截 | +90 |
| `woc/__main__.py` | 增加 `run` 子命令分发 | +8 |
| `woc/context.py` | `lint_templates` 支持 `runnable: true`（不再报"多行会被截断"） | +12 |
| `README.md` | 一节说明 + 与模板的边界 | +40 |
| **复用** | `load_templates` / `extract_code_blocks` / `command_lines` / `run_command_capture` / `interactive_input` | 0 |

合计约 **+150 行**。如果另建 `.woc/tasks/` 目录 + 新 front matter + 新 loader + 新安装命令，
大约要 **+350 行**，且两套格式长期漂移 —— 不建议。

### 5.4 收益

1. **0 token 的确定性流程**：跑 5 个 stage 花 0 次 API 调用；同样的 loop 要 5 次。
2. **可审阅、可 diff、可全局安装**：复用 `--install-templates`，task 和模板一起版本化。
3. **补齐"模板里那段命令其实是流程"的现实**：现在的模板只能"给模型看"，模型可能抄错；
   task 是"人确认过的原样执行"。
4. **与 loop 互补**：探索性任务（不知道下一步跑什么）用 loop；重复性流程（步骤固定）用 task。

### 5.5 成本与风险

1. **信任模型变了**：README 现在写"模板只是文本，**不会被执行**"。加了 runnable 之后，
   clone 来的仓库里就可能有**可执行**内容 —— 这是本项目第一次让"外部文件"具备执行语义，
   必须靠"显式 `woc run` + 逐 stage 确认"兜住，README 也要改写。
2. **表达力受限**：无共享状态 → 不能写真正的脚本，只能写"命令清单"。复杂流程会想逃到 loop。
3. **会腐化**：路径、服务名、命令参数都会过期。需要 `--check-templates` 覆盖 task，
   并且接受"task 跑失败"是常态。
4. **概念重叠**：`template` / `task` / `loop` 三个词容易让人犹豫用哪个，
   README 需要一句话讲清：**参考用 template，重复流程用 task，探索用 loop**。

### 5.6 什么时候值得做

判断标准（和 §7 的 loop 判定同构）：

> 如果一周内你**手打同一串 ≥3 条命令 ≥2 次**，就值得写成 task；否则不要加。

顺序建议：**先落地 loop**（把 `execute_step(cmd, tier)` 抽成可复用函数），
task 再作为它的"无 LLM 驱动"薄薄一层 —— 这样两者共享执行语义，不会各写一套。

---

## 6. 待拍板（按重要性排序）

| # | 决定 | 我的推荐 |
|---|---|---|
| 1 | 协议：`#STEP:` 还是 `#DONE:` | **`#STEP:`**（普通回合 0 额外成本） |
| 2 | `wocc` 默认是否开 loop | **默认关**，`--loop` 或 `[loop] enabled=true` 开 |
| 3 | `auto_safe`（只读单命令免确认）默认值 | **false**（每步仍按回车） |
| 4 | 步数上限 | **5** |
| 5 | 累计预算 | **24000 字符** |
| 6 | sudo 无 ticket 的步骤 | **不执行 → 回灌 126 → 计一步** |
| 7 | 是否现在做 task | **等 loop 稳定后** |
| 8 | `find` / `sort` 是否留在 `safe` | **踢掉**（少 2 个白名单，换 0 误判） |

第 3 条是体验分水岭：`auto_safe=false` 时 loop 省的是"重新描述问题"，不是"按回车"。
如果你希望它真的自动跑完只读命令，第 3 条改成 true，但请连同 §5.5 的风险一起看。

---

## 7. 验收（实现后必须跑）

1. 语法 + 模板自检：`compileall`、`--check-templates`（0 error / 2 warning）。
2. **前缀缓存不退化**：`hit≈3456`，miss 只随用户问题增长。
3. 单轮 `woc` 行为回归：`printf '\n' | python3 woc.py "..."` 与改动前逐字节一致。
4. loop 专项（新测试，无网络可跑的部分用假 LLM 桩）：
   - `#STEP:` 正常续步、`#CHAT:` 正常收尾、普通命令正常结束回合
   - 步数到顶 / 预算到顶 / 重复命令三种中止都有提示
   - `find . -exec rm {} +` → `confirm`；`sort -o x` → `confirm`；`ls -l` → `safe`
   - `sudo -i` 在 loop 里仍是 `deny`（126，不执行）
5. 真实 API 跑一次 3 步任务，确认总 token 与 §4.3 的估算同量级。

---

## 8. 实施记录（2026-10-18）

已按 §6 的推荐实现，落地的决定与理由：

| # | 决定 | 落地 |
|---|---|---|
| 1 | `#STEP:` 协议 | ✅ 普通回合仍 1 次请求，实测未退化 |
| 2 | `--loop` 开启 | ✅ `[loop] enabled` / `WOC_LOOP` |
| 3 | `auto_safe=false` | ✅ 默认每步确认 |
| 4 | 步数 5 | ✅ 硬上限 8 |
| 5 | 预算 24000 字符 | ✅ 越界显式提示 |
| 6 | sudo 无 ticket | ✅ 不执行 → 回灌 126 → 计一步 |
| 7 | task | ⏸ 本阶段不做 |
| 8 | `find` / `sort` 移出白名单 | ✅ 白名单只留真·只读命令 |

实现落在：`woc/loop.py`（新增 266 行）、`woc/cli/multi.py`（84 行，原 237 行）、
`woc/llm.py`（`StreamFilter` 认 `#STEP:`）、`woc/prompts.py`（`LOOP_HINT`）、
`woc/config.py`（`[loop]`）、`woc/colors.py`（修管道 stdin 的二次 reconfigure 崩溃）。

实测结果：

- 单轮 `woc`：`woc --help` 启动 160 个模块、`woc.loop` **未被导入**（延迟导入生效），
  行为与改动前一致（真跑一条命令验证过）。
- 前缀缓存：loop 关闭 8707 字符 / hit=3456；loop 开启 9543 字符 / hit=3712，两次调用都稳定。
- 真机 2 步任务（` --loop`）：模型输出 `#STEP:` → 执行 → `#CHAT:` 收尾，退出码 0。
- 单元测试：`risk_tier` 16 例、`StreamFilter` 6 例、loop 驱动 10 组（步数上限、重复命令、
  危险命令、预算、auto_safe、关闭状态）全部通过。

### 8.1 上线后回归：`#CHAT:` 标记遗忘（已修）

**现象**：进入 `wocc` 后，模型回答问题时忘记写 `#CHAT:` 的次数变多；忘记之后
`sanitize` 会把正文当成命令，提示用户按 Enter 执行。

**排查**（3 组问题 × 6 轮纯问答会话，temperature=0）：

| 配置 | 带标记 |
|---|---|
| loop=off，assistant 历史里剥掉标记（旧行为） | 9/12 |
| loop=on，assistant 历史里剥掉标记（旧行为） | 11/18 |
| loop=on，assistant 历史里**保留** `#CHAT:` | **18/18** |
| loop=off，历史保留（对照） | 6/6 |

**机制**：模型会模仿自己上一轮的回复格式。第一次漏标记后，这条“裸正文”进了
assistant 历史，后续轮次继续漏 —— set3 就是 turn3 漏了之后 turn4/5/6 全漏，是连锁反应。
loop 模式下 assistant 历史里还有更多被剥掉标记的命令（`#STEP:` 同理），进一步把
格式先验推向“不要写标记”。

**修复**：`woc/loop.py` 把 `#CHAT: ` 连同正文一起写进 assistant 历史（只影响历史，
不影响终端显示），代价约 3 tokens/轮。修复后同一组 6 问 6 答：0 次误执行。

**未采用的方案**：
- 在 hint 末尾加“必须带 #CHAT:”硬规则：无标记时 5/6，不够稳定，且又是一条要维护的规则。
- `sanitize` 启发式兜底（纯中文行不是命令）：只覆盖部分失败形态，还会误伤
  `echo 你好` 这类合法命令，暂不加。
