# 模板参考库

模板是写给模型的参考文本，用来把你在这个环境里攒下来的命令固定下来。安装与总览见 [README.md](../README.md)。

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
