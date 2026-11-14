# 企业微信播报模板

两个事件各一张卡片，都由 [teriyakisushi/wecom-notifier](https://github.com/teriyakisushi/wecom-notifier)
这个 composite action 渲染并发送：

| 文件 | 触发方 | 事件 |
| --- | --- | --- |
| `wecom-workflow_run.yml` | `.github/workflows/notify.yml` | `workflow_run`：test 跑完后报 CI 结果（绿/红/取消/进行中） |
| `wecom-push.yml` | `.github/workflows/test.yml` 的 `push-notify` job | `push`：每次推送报提交与 compare 链接 |

模板文件名以 `-<事件名>.yml` 结尾，action 的 `auto` 预设靠这个自动命中，不需要显式写
`template:`。改文案直接改这两个文件；改完想先看效果，用 wecom-notifier 仓库的 dist 跑一次：

```bash
WECOM_INPUTS='{"dry-run":"true"}' \
GITHUB_EVENT_NAME=push \
GITHUB_EVENT_PATH=tests/fixtures/events/push.json \
node dist/index.mjs
```

没有配置 `WECOM_WEBHOOK` secret 时 job 会跳过并留一条 notice，fork / PR 不受影响。
卡片长度、`card_image.aspect_ratio` 之类硬限制由 action 侧校验，超了会在日志里报。
