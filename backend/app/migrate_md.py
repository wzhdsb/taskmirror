"""一次性迁移: 旧 task-board 任务卡 → SQLite。只读旧目录, 幂等(同 id 跳过)。
用法: python -m app.migrate_md "D:\\projects\\task-board\\tasks" [--dry-run]
done/ 子目录卡 → status=完成。正文 ## 进展日志 段拆成 task_events(无年份取 created 年份)。"""
import re
import sys
import time
from pathlib import Path

from . import db, service
from .service import LOG_LINE_RE


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def parse_card(text: str):
    """移植自 board.py(23 例测试语义): frontmatter 手写解析, 只切第一个冒号, 剥成对引号。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    meta, end = {}, None
    for i in range(1, min(len(lines), 61)):
        ln = lines[i]
        if ln.strip() == "---":
            end = i
            break
        if ":" not in ln:
            return None
        k, v = ln.split(":", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        meta[k.strip()] = v
    if end is None or "id" not in meta:
        return None
    meta.setdefault("title", meta["id"])
    meta.setdefault("status", "待执行")
    meta.setdefault("owner", "")
    meta.setdefault("created", _now())
    meta.setdefault("updated", meta["created"])
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return meta, body


def split_log(body: str):
    """正文 → (纯正文, [日志行]); 无 ## 进展日志 段则原样返回。"""
    m = re.search(r"^##\s*进展日志\s*$", body, re.MULTILINE)
    if not m:
        return body, []
    head, tail = body[: m.start()], body[m.end():]
    return head.rstrip(), [ln.strip() for ln in tail.splitlines() if ln.strip()]


def main(src_str: str, dry: bool = False, refresh: bool = False):
    db.migrate()
    src = Path(src_str)
    files = sorted(src.glob("*.md")) + sorted((src / "done").glob("*.md"))
    n_new = n_skip = n_ev = n_ref = 0
    for fp in files:
        parsed = parse_card(fp.read_text(encoding="utf-8"))
        if not parsed:
            print(f"跳过(无法解析): {fp.name}")
            continue
        meta, body = parsed
        body = re.sub(r"^#\s+[^\n]+\n+", "", body, count=1)  # 剥正文头 H1(镜像渲染会注入 # title, 避免双标题)
        done = fp.parent.name == "done"
        body, log_lines = split_log(body)
        year = (meta.get("created") or "")[:4] or time.strftime("%Y")
        events = [(meta["created"], meta.get("owner") or "旧系统", "created",
                   {"title": meta["title"]})]
        for ln in log_lines:
            m = LOG_LINE_RE.match(ln)
            if m:
                ts, who, text = m.groups()
                events.append((f"{year}-{ts}:00", who, "log", {"text": text}))
            else:  # 不合规日志行不丢, 留在正文尾部
                body += f"\n> {ln}"
        status = "完成" if done else meta.get("status", "待执行")
        if status != "待执行":
            events.append((meta.get("updated") or _now(), meta.get("owner") or "旧系统",
                           "status", {"from": "待执行", "to": status}))
        if dry:
            print(f"[dry] {meta['id']} → {status} ({len(events)} 事件)")
            continue
        if refresh:  # 切换期用: 新系统尚无人写, 以旧目录为准覆盖重导(镜像同路径被重渲染覆盖)
            c = db.conn()
            with c:
                c.execute("DELETE FROM task_events WHERE task_id=?", (meta["id"],))
                c.execute("DELETE FROM tasks WHERE id=?", (meta["id"],))
        if service.import_task(meta["id"], meta["title"], status, meta.get("owner", ""),
                               meta["created"], meta["updated"], body, events):
            n_new += 1
            n_ev += len(events)
            if refresh:
                n_ref += 1
        else:
            n_skip += 1
    print(f"导入 {n_new} 张(事件 {n_ev}), 跳过已存在 {n_skip} 张" + (f", 其中覆盖重导 {n_ref} 张" if refresh else ""))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], "--dry-run" in sys.argv, "--refresh" in sys.argv)
