# MCP 接入与排障

注册（Windows，git-bash 里注意 `/c` 会被转路径，加 `MSYS_NO_PATHCONV=1`）：

```bash
MSYS_NO_PATHCONV=1 claude mcp add taskmirror -s user -- cmd /c D:\Anaconda\python.exe D:\projects\taskmirror\backend\run_mcp.py
claude mcp list   # 应显示 ✔ Connected
```

- 数据目录由 `TASKBOARD_HOME` 决定，缺省 = 仓库 `data/`（与 Web 服务同一份）。
- 包版本：`mcp` 1.x 与 2.x 均兼容（2.x 中 FastMCP 改名 MCPServer，`mcp_server.py` 里做了双 import）。
- 排障：stdio 下看不到输出——先手动跑 `python backend/run_mcp.py`，正常是静默挂起等 stdin；报错则按栈修。连接超时多半是 python 路径或包没装到该解释器。
- 工具清单见 README；`take_task` 原子防抢（`WHERE owner='' OR owner=:owner`），冲突返回 409 文案。
