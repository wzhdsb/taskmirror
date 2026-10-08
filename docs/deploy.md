# 部署

## 本机常驻（Windows）

开机自启：Startup 目录放 `taskmirror.vbs`（无窗口 pythonw）：

```vbs
CreateObject("WScript.Shell").Run "cmd /c cd /d <repo>\backend&& set PYTHONUTF8=1&& <pythonw.exe> -m app.main", 0, False
```

端口由 `TASKBOARD_PORT` 决定（缺省 8801）。日志：`data/logs/app-YYYYMMDD.jsonl`。

## 环境变量

| 变量 | 缺省 | 说明 |
|---|---|---|
| `TASKBOARD_HOME` | `<repo>/data` | DB/镜像/备份/日志全落这里 |
| `TASKBOARD_PORT` | 8801 | 监听端口 |
| `TASKBOARD_HOST` | 127.0.0.1 | 绑定地址 |
| `TASKBOARD_TOKEN` | 空=免鉴权 | 设了则 `/api/*` 校验 Bearer（SSE 走 `?token=`） |

## Docker

```bash
docker build -t taskmirror .
docker run -d -p 8801:8801 -v taskmirror-data:/data taskmirror
```

## 备份与数据史

- 自动：每 50 次写一次在线备份到 `data/backups/`（留 20 份）；启动/每 6h 可再加 cron。
- 手动：`python dev.py backup`。
- 卡片历史：在 `data/` 里 `git init && git add -A && git commit` 定期提交，md 镜像的 git log 即完整变更史。
