---
id: top-ip-from-log
desc: 按客户端 IP 统计访问量前 N 名，支持自定义日志路径与 N
tags: [nginx, 日志, 统计, awk, 访问量, log]
when: 用户问"访问量最高的 IP / 哪个 IP 请求最多 / 分析访问日志"
---

# 场景

分析 Web 访问日志（nginx/apache combined 格式），找出请求量最高的客户端。

# 参考命令（作者标注，未经验证）

```bash
awk '{print $1}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head -n 10
```

# 约定

- combined 格式下客户端 IP 固定在第 1 列。
- 默认取前 10；用户说了别的数字就用那个数字。
- 日志路径不确定时，先确认存在再统计：

```bash
f=/var/log/nginx/access.log; test -r "$f" && awk '{print $1}' "$f" | sort | uniq -c | sort -rn | head -n 10 || echo "读不到 $f"
```

# 不要这样做

- 不要用 `cut -d' ' -f1`：combined 格式里请求行本身含空格，某些变体下会切错列。
  `awk '{print $1}'` 按空白切分，更稳。
- 不要用 `cat file | awk ...`：多一次无谓的进程。
- 不要在日志很大（>1G）时省略 `head`：`sort` 会吃满内存。

# 变体

- 按 URL 统计：`awk '{print $7}' 日志 | sort | uniq -c | sort -rn | head -n 10`
- 只看状态码非 200：`awk '$9 != 200 {print $1}' 日志 | sort | uniq -c | sort -rn | head`
- 实时跟：`tail -f 日志 | awk '{print $1}'` —— **不要用**，会一直阻塞不返回。
