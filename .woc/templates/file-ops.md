---
id: file-ops
desc: 读写和改文件用 woc-fs（read / write / edit），不要用 cat、sed -i 和 heredoc
tags: [文件, 读取, 写入, 修改, 编辑, 替换, read, write, edit, 改代码, 保存]
when: 需要看文件内容、新建文件、或把文件里某段文字换掉
---

# 场景

用户让你看代码、改配置、加一个函数、把某个字符串换掉。这些事都能用 `cat`、
`sed -i`、`cat > f <<'EOF'` 干，但它们在这个场景里有三个硬伤：

- `cat` 一个 500 行的文件 ≈ 8k tokens 进对话，而且下一轮还得再读一遍。
- `sed -i` 的正则要挨个逃逸 `/`、`&`、`.`、`*`，而且"命中 17 处"是静默的。
- heredoc 在内容本身含 `EOF` 时直接断，回灌进对话还会二次损坏。

`woc-fs` 把这三件事拆成三个无状态子命令，一条命令一个动作，结果只回一行摘要。

# 三个子命令

读：默认前 60 行，带行号，**必须**用 `--lines` 或 `--head` 收窄。

```bash
woc-fs read PATH --lines 40:80
```

写整份文件：`--content` 支持 `@file`（从文件读）和 `-`（从 stdin 读），
长内容优先走 `@` 或 stdin，别塞进命令行。

```bash
woc-fs write PATH --content @/tmp/patch.txt --mkdir --backup
```

字面量替换：`--old` 默认必须在文件里**只出现一次**，否则直接报错并告诉你命中几处、在哪几行；
要改重复文本里的某一处就加 `--occurrence N`（第 N 个命中，1 基），全改就 `--all`。
改成功后命令会回显改动处的行号与上下文，改动行前缀是 `> `，不用再 `read` 一遍确认。

```bash
woc-fs edit PATH --old 'return None' --new 'return []' --backup
```

# 多行内容

命令行走单行，多行内容别硬塞引号：加 `--escapes`，把换行写成 `\n`（`\t` 也认，
`\\` 表示反斜杠本身）。

```bash
woc-fs edit PATH --old 'if a:\n    b\n' --new 'if a:\n    b = 1\n' --escapes
```

十几行以上的整块，先落成临时文件再 `@` 进去：

```bash
woc-fs edit PATH --old @/tmp/woc-old.txt --new @/tmp/woc-new.txt
```

# 约定

- 先 `read` 再 `edit`：没看到原文就猜 `--old`，十次有八次是"没找到"。
- `--old` 写长一点更安全：带上前后文和缩进，比 `x = 1` 这种短串稳。
- 改之前加 `--backup`：备份不会互相覆盖（`PATH.bak`、`PATH.bak.1`…），摘要里会写明落到哪个文件。
- 拿不准时先跑 `--dry-run`：只报摘要不落盘。
- 退出码 0 成功、1 失败、2 参数错；失败时 stderr 只有一行 `error: …`，照那行改。

# 不要这样做

- 不要用 `cat` 看大文件、不要用 `sed -i` 改文件 —— 已经跟你说过为什么了。
- 不要用 `cat > f <<'EOF'` 写文件：内容里有 `EOF` 就断，而且这个仓库的确认流程会把
  多行命令截成第一行。
- 不要拿 `>>` 往代码文件里追加：位置不可控，十次有九次追加到了文件末尾。
- 不要用 `read --raw` 看代码：没行号的输出没法定位，`edit` 时会用到行号。
- 不要一条命令里连做三件事（读、改、验证），每步都会被单独确认，分开写反而更快。
