"""DB → md 镜像: 人读视图 + git 友好备份。DB 是唯一真源, 镜像永远可由 events 重渲染。
旧 board.py 兼容: frontmatter 前六字段与旧格式一致, 过渡期旧服务仍能读新镜像。
写策略: 每次写操作提交后同步原子写(tmp 不带 .md 后缀 + os.replace, Windows 已验证);
启动 full_sync 对账自愈; 镜像被人改 → 下次写操作静默覆盖(文件头已声明)。"""
import json
import os
import threading
import time
from pathlib import Path

from . import config, db

_NOTICE = "<!-- TaskMirror 自动生成镜像, 以数据库为准; 人工修改会被覆盖 -->"


def _dirs() -> dict[str, Path]:
    root = config.home() / "tasks"
    return {"active": root, "done": root / "done", "trash": root / "trash"}


def _atomic_write(fp: Path, text: str):
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.parent / f".tmp-{os.getpid()}-{threading.get_ident()}"
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, fp)


def _fm_val(v: str) -> str:
    return f'"{v}"' if v else '""'


def _event_line(e: dict) -> str:
    ts = (e.get("ts") or "")[5:16]  # MM-DD HH:MM
    try:
        d = json.loads(e.get("detail") or "{}")
    except ValueError:
        d = {}
    ev, txt = e.get("event"), ""
    if ev == "created":
        txt = "建卡"
    elif ev == "take":
        txt = f"认领 (owner={d.get('owner', '')})"
    elif ev == "status":
        txt = f"状态 {d.get('from', '')}→{d.get('to', '')}"
    elif ev == "owner":
        txt = f"owner 置为 {d.get('to', '')}" if d.get("to") else "owner 清空"
    elif ev == "body":
        txt = "更新正文"
    elif ev == "title":
        txt = f"标题改为「{d.get('to', '')}」"
    elif ev == "labels":
        txt = f"标签 → {'、'.join(d.get('to') or []) or '无'}"
    elif ev == "priority":
        txt = f"优先级 → {d.get('to', '')}"
    elif ev == "due":
        txt = f"截止 → {d.get('to') or '无'}"
    elif ev == "color":
        txt = f"颜色 → {d.get('to') or '默认'}"
    elif ev == "log":
        txt = d.get("text", "")
    elif ev == "restore":
        txt = "恢复到待执行"
    elif ev == "imported":
        txt = "从旧系统导入"
    elif ev == "relate":
        txt = f"{d.get('kind', '')} {'−' if d.get('removed') else '→'} {d.get('target', '')}"
    elif ev == "deleted":
        txt = "删除卡片"
    else:
        txt = ev or ""
    return f"- {ts} [{e.get('actor', '')}] {txt}"


def render(task: dict, events: list[dict], tail: str = "") -> str:
    fm = [
        "---",
        f"id: {task['id']}",
        f"code: {task.get('code') or ''}",
        f"title: {task['title']}",
        f"status: {task['status']}",
        f"owner: {_fm_val(task.get('owner') or '')}",
        f"created: {task['created']}",
        f"updated: {task['updated']}",
    ]
    if task.get("priority") and task["priority"] != "normal":
        fm.append(f"priority: {task['priority']}")
    if task.get("labels"):
        fm.append(f"labels: {json.dumps(task['labels'], ensure_ascii=False)}")
    if task.get("due"):
        fm.append(f"due: {task['due']}")
    if task.get("color"):
        fm.append(f"color: {task['color']}")
    if task.get("related"):
        fm.append(f"related: {','.join(task['related'])}")
    if task.get("depends"):
        fm.append(f"depends: {','.join(task['depends'])}")
    fm.append("---")
    lines = fm + ["", _NOTICE, "", f"# {task['title']}", ""]
    body = (task.get("body") or "").strip()
    if body:
        lines += [body, ""]
    lines.append("## 进展日志")
    lines.append("")
    lines += [_event_line(e) for e in events]
    if tail:
        lines.append(tail)
    return "\n".join(lines) + "\n"


def sync(task: dict, events: list[dict]):
    """状态变化时切换落点: 先写新位再删旧位(崩溃方向=双份, 不丢卡)。"""
    d = _dirs()
    name = f"{task['id']}.md"
    text = render(task, events)
    if task["status"] == "完成":
        _atomic_write(d["done"] / name, text)
        (d["active"] / name).unlink(missing_ok=True)
    else:
        _atomic_write(d["active"] / name, text)
        (d["done"] / name).unlink(missing_ok=True)


def trash(task: dict, events: list[dict]):
    """删除卡镜像入 trash(带时间戳防同 id 复用覆盖), 并清掉活动/完成位。"""
    d = _dirs()
    events = list(events) + [{"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "actor": "system", "event": "deleted", "detail": "{}"}]
    _atomic_write(d["trash"] / f"{task['id']}-{int(time.time())}.md", render(task, events))
    name = f"{task['id']}.md"
    (d["active"] / name).unlink(missing_ok=True)
    (d["done"] / name).unlink(missing_ok=True)


def full_sync():
    """启动对账: 以 DB 覆盖全部镜像, 删除 DB 中不存在的镜像文件(trash 除外)。"""
    c = db.conn()
    rows = c.execute("SELECT * FROM tasks").fetchall()
    keep = {"active": set(), "done": set()}
    for row in rows:
        t = dict(row)
        for k in ("labels", "related", "depends"):
            try:
                t[k] = json.loads(t.get(k) or "[]")
            except ValueError:
                t[k] = []
        evs = [dict(e) for e in c.execute(
            "SELECT ts, actor, event, detail FROM task_events WHERE task_id=? ORDER BY id",
            (t["id"],))]
        sync(t, evs)
        keep["done" if t["status"] == "完成" else "active"].add(f"{t['id']}.md")
    d = _dirs()
    for kind in ("active", "done"):
        for fp in d[kind].glob("*.md"):
            if fp.name not in keep[kind]:
                fp.unlink(missing_ok=True)
