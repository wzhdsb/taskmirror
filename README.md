# TaskMirror

**AI-agent-native task board**: one SQLite database, two front doors — a fast web kanban for humans and an [MCP](https://modelcontextprotocol.io) server for agents. Agents create/take/deliver task cards; you watch it all live in the browser.

```
Claude session (any dir) ──stdio MCP──▶ ┌ service.py ── SQLite (WAL) ──┐
Browser ──REST + SSE────▶               │        ▶ md mirror (git-able) │
(React SPA served by backend)           └───────────────────────────────┘
```

Two independent processes share the same DB. **If the web server dies, agents keep working** — MCP writes SQLite directly, and md mirrors under `data/tasks/` are re-rendered on next write.

[中文说明](README.zh-CN.md)

![board](docs/img/board-dark.png)
![monitor](docs/img/monitor-dark.png)

## Features

- **Kanban web UI** — dark/light, drag & drop (dnd-kit), right drawer with markdown body editing, labels/priority/due/color, live updates over SSE
- **Monitor tab** — per-owner load, stalled cards, stage-duration averages, live event stream, rule-based advisor suggestions (`GET /api/insights`)
- **8 MCP tools** — `list_tasks` `get_task` `create_task` `take_task` (atomic claim) `update_task` `set_status` `add_log` `get_insights`
- **Durable by construction** — WAL mode + `busy_timeout`, append-only event log (the single source of progress history), auto backups (every 50 writes, keep 20), atomic mirror writes, JSON access logs
- **Human-readable mirror** — every card is also a plain markdown file you can read/grep/git; DB is truth, mirrors are projections
- Optional single-token auth (`TASKBOARD_TOKEN`), Dockerfile included

## Quick start (local)

```bash
git clone <repo> && cd taskmirror
python dev.py setup          # pip install -e backend[dev] + npm ci
python dev.py build          # vite build → served by backend
python dev.py serve          # http://127.0.0.1:8801
```

Register the MCP server for Claude Code (Windows):

```bash
MSYS_NO_PATHCONV=1 claude mcp add taskmirror -s user -- cmd /c <python.exe> <repo>\backend\run_mcp.py
```

Linux/macOS: drop the `cmd /c`. Then in any Claude session: *"用 taskmirror 建卡：..."*

## Card format

Markdown body with four sections agents understand:

```
## ① 任务      ← user's words, verbatim (the contract)
## ② 已知事实   ← facts, so nobody re-researches
## ③ 交付物     ← what "done" means
## ④ 注意      ← red lines, pitfalls
```

Status flow: 待执行 → 执行中 (claim) → 待验收 (deliver) → 完成 (accept). Executors stop at 待验收; only the acceptor archives.

## Development

```bash
python dev.py test           # backend pytest + frontend type-check/build
python dev.py e2e            # Playwright full-flow smoke (needs `python dev.py build` first)
python dev.py migrate-legacy "path/to/old/tasks" [--refresh]   # import legacy markdown cards
python dev.py backup         # snapshot DB
```

Data lives in `data/` (gitignored). **Run `git init` inside `data/` for free card-history diffs** — mirrors are the only content, so the git log *is* your change history. Backups land in `data/backups/`.

## Concurrency budget (for agent dispatchers)

GLM/Claude accounts have small concurrent-request caps (e.g. 4). The bundled `/dispatch` command pattern: cap in-flight subagents (default 4), queue the rest in waves, and switch excess agents to a faster/cheaper model tier. 429 / error 1302 = cap hit → queue, don't retry-storm.

## License

[MIT](LICENSE)
