---
id: git-recent-changes
desc: 汇总本仓库最近的改动：改了哪些文件、改了多少、以及 diff 概览
tags: [git, 提交, 改动, diff, 历史]
when: 用户问"最近改了什么 / 上次提交是什么 / 这个改动动了哪些文件"
---

# 场景

用户想快速了解仓库最近发生了什么，不想自己敲 `git log` + `git diff`。

# 参考命令（已验证）

```bash
git log --oneline -n 10; echo '--- files ---'; git show --stat --oneline HEAD
```

带工作区未提交改动：

```bash
git status --short; echo '--- diff ---'; git diff --stat
```

# 约定

- 先用 `--oneline` 给出可扫读的历史，再补 `--stat` 的文件清单。
- 用 `echo '--- ... ---'` 做分隔，否则两段输出会粘在一起，难以阅读。
- 只看"某个文件历史上怎么变的"时用 `git log -p --follow -- <路径>`。

# 不要这样做

- 不要用裸 `git log`：默认进分页器（`less`），会把 `woc` 卡住。必须加
  `--oneline`、`-n`，或显式 `--no-pager`。
- 不要在没确认仓库状态时输出 `git diff HEAD~5`：如果提交数不足会直接报错。
- 不要用 `git status` 的完整输出（会带上大段帮助文字）；用 `--short`。
- 不要输出 `git diff` 的完整正文：大改动会刷屏并占用大量上下文，
  用 `--stat`，用户想看细节时再单独要。

# 注意

`git diff` 和 `git show` 都走分页器。凡是可能触发 `less` 的 git 子命令
（`log` / `diff` / `show` / `blame`），都要保证输出是有限的：加 `-n`、
`--stat` 或 `--no-pager`。这是这个模板里最容易踩的坑。
