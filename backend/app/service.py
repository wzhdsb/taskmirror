"""全部业务逻辑: task CRUD / move / take / log + task_events 追加 + rev + 镜像触发。
Web(api.py) 与 MCP(mcp_server.py) 两个进程共用本模块, 各自直写同一 SQLite(WAL)。
写路径: 进程内锁串行 + 短事务; 跨进程靠 busy_timeout(5s), 单机两进程远达不到瓶颈。
ponytail: 若未来出现写冲突 SQLITE_BUSY, 在此加一次重试即可, 不需要队列。"""
import json
import re
import sqlite3
import threading
import time

from . import db, mirror

STATUSES = ("待执行", "执行中", "待验收", "完成")
PRIORITIES = ("low", "normal", "high", "urgent")
STATUS_ORD = {s: i for i, s in enumerate(STATUSES)}
LOG_LINE_RE = re.compile(r"^- (\d{2}-\d{2} \d{2}:\d{2}) \[(.+?)\] (.*)$")
_lock = threading.Lock()


class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def slugify(title: str) -> str:
    s = re.sub(r"[^0-9A-Za-z一-鿿]+", "-", (title or "").strip())
    return s.strip("-")[:40] or "task"


def _norm_owner(v) -> str:
    return re.sub(r'[ "\'\n]', "", v or "")[:40]


def _check_due(due):
    if due and not re.match(r"^\d{4}-\d{2}-\d{2}$", due):
        raise ApiError(400, "due 需为 YYYY-MM-DD")


def _norm_labels(labels) -> list[str]:
    return [str(x).strip()[:24] for x in (labels or []) if str(x).strip()][:8]


def _serialize(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["labels"] = json.loads(d.get("labels") or "[]")
    except ValueError:
        d["labels"] = []
    return d


def _get(c: sqlite3.Connection, cid: str) -> sqlite3.Row:
    row = c.execute("SELECT * FROM tasks WHERE id=?", (cid,)).fetchone()
    if not row:
        raise ApiError(404, "任务不存在")
    return row


def _ev(c, task_id, event, detail, actor="webui"):
    c.execute("INSERT INTO task_events(task_id, ts, actor, event, detail) VALUES(?,?,?,?,?)",
              (task_id, now(), (actor or "webui")[:40], event, json.dumps(detail, ensure_ascii=False)))


def _load_for_mirror(c, cid):
    row = _get(c, cid)
    evs = [dict(e) for e in c.execute(
        "SELECT id, ts, actor, event, detail FROM task_events WHERE task_id=? ORDER BY id", (cid,))]
    return _serialize(row), evs


def _after_write(cid=None, task_events=None):
    """事务提交后: 同步镜像 + 计数备份。"""
    if cid is not None:
        mirror.sync(task_events[0], task_events[1])
    db.maybe_backup()


# ---------- 读 ----------

def board() -> dict:
    c = db.conn()
    rows = [_serialize(r) for r in c.execute(
        "SELECT * FROM tasks WHERE board_id=1 AND status!='完成' ORDER BY position, created")]
    rows.sort(key=lambda t: (STATUS_ORD.get(t["status"], 9), t["position"]))
    done = [_serialize(r) for r in c.execute(
        "SELECT * FROM tasks WHERE board_id=1 AND status='完成' ORDER BY updated DESC LIMIT 50")]
    return {"tasks": rows, "done": done, "rev": db.get_rev()}


def detail(cid: str) -> dict:
    c = db.conn()
    task, evs = _load_for_mirror(c, cid)
    for e in evs:
        try:
            e["detail"] = json.loads(e.get("detail") or "{}")
        except ValueError:
            e["detail"] = {}
    return {"task": task, "body": task["body"], "events": evs}


def recent_events(limit: int = 100) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    c = db.conn()
    return [dict(r) for r in c.execute(
        "SELECT e.id, e.task_id, t.title, e.ts, e.actor, e.event, e.detail "
        "FROM task_events e LEFT JOIN tasks t ON t.id=e.task_id ORDER BY e.id DESC LIMIT ?", (limit,))]


# ---------- adviser 规则层: 纯读派生, 数据零新增 ----------

STALL_HOURS = 4     # 执行中无动静视为停滞
REVIEW_HOURS = 24   # 待验收滞留
DUE_SOON_DAYS = 3


def _parse_ts(ts: str) -> float | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(ts, fmt))
        except ValueError:
            continue
    return None  # 迁移旧卡可能秒级缺失, 兼容三分辨率


def _age_hours(ts: str) -> float:
    t = _parse_ts(ts)
    return (time.time() - t) / 3600 if t else 0.0


def _due_days(due: str) -> float:
    return (time.mktime(time.strptime(due, "%Y-%m-%d")) - time.time()) / 86400


def insights() -> dict:
    """规则层建议: 停滞/滞留/due 临近/负载/空列。LLM 层=主会话读本结果再拆卡。"""
    c = db.conn()
    rows = [_serialize(r) for r in c.execute(
        "SELECT * FROM tasks WHERE board_id=1 AND status!='完成'")]
    stage_avg = _stage_durations(c)
    load: dict[str, dict] = {}
    for t in rows:
        if t["owner"] and t["status"] in ("执行中", "待验收"):  # 待执行不算在办
            d = load.setdefault(t["owner"], {"owner": t["owner"], "active": 0, "review": 0})
            d["active" if t["status"] == "执行中" else "review"] += 1
    stalled = [{"id": t["id"], "title": t["title"], "owner": t["owner"], "hours": round(_age_hours(t["updated"]), 1)}
               for t in rows if t["status"] == "执行中" and _age_hours(t["updated"]) > STALL_HOURS]
    review_overdue = [{"id": t["id"], "title": t["title"], "owner": t["owner"],
                       "hours": round(_age_hours(t["updated"]), 1)}
                      for t in rows if t["status"] == "待验收" and _age_hours(t["updated"]) > REVIEW_HOURS]
    due_soon = [{"id": t["id"], "title": t["title"], "due": t["due"], "status": t["status"]}
                for t in rows if t["due"] and 0 <= _due_days(t["due"]) <= DUE_SOON_DAYS]
    overdue = [{"id": t["id"], "title": t["title"], "due": t["due"], "status": t["status"]}
               for t in rows if t["due"] and _due_days(t["due"]) < 0]
    suggestions = []
    if review_overdue:
        suggestions.append(f"{len(review_overdue)} 张卡待验收滞留超 {REVIEW_HOURS}h, 请验收或打回")
    if stalled:
        who = ", ".join(sorted({t["owner"] or "无主" for t in stalled}))
        suggestions.append(f"{len(stalled)} 张执行中卡停滞超 {STALL_HOURS}h ({who}), 催 add_log 心跳或换人")
    if overdue:
        suggestions.append(f"{len(overdue)} 张卡已过 due, 优先处理")
    if due_soon:
        suggestions.append(f"{len(due_soon)} 张卡 {DUE_SOON_DAYS} 天内到期")
    if not any(t["status"] == "待执行" for t in rows) and not stalled:
        suggestions.append("待执行列为空且无停滞, 可让 adviser 拆下一批卡")
    busy = max((d["active"] for d in load.values()), default=0)
    idle = [o for o, d in load.items() if d["active"] == 0 and d["review"] == 0]
    if idle and busy >= 3:
        suggestions.append(f"{', '.join(idle)} 当前空闲, 可派新卡")
    return {"generated": now(), "counts": {s: sum(1 for t in rows if t["status"] == s) for s in STATUSES[:3]},
            "stage_avg": stage_avg,
            "load": sorted(load.values(), key=lambda d: -d["active"]),
            "stalled": stalled, "review_overdue": review_overdue,
            "due_soon": due_soon, "overdue": overdue, "suggestions": suggestions}


def _stage_durations(c: sqlite3.Connection) -> dict:
    """各状态平均停留(h): 相邻 status 事件时间差。末段(仍在进行)不计, 停滞另有清单。"""
    seq: dict[str, list[tuple[str, str, dict]]] = {}  # tid -> [(ts, 事发状态, detail)]
    for r in c.execute("SELECT task_id, ts, detail, event FROM task_events "
                       "WHERE event IN('created','status') ORDER BY id"):
        seq.setdefault(r["task_id"], []).append(
            (r["ts"], "待执行" if r["event"] == "created" else
             json.loads(r["detail"] or "{}").get("from", ""), json.loads(r["detail"] or "{}")))
        # status 事件到达即离开 from 状态; created 事件 = 离开"待执行"建卡瞬间, 不算
    spans: dict[str, list[float]] = {}
    for evs in seq.values():
        for i in range(1, len(evs)):
            frm = evs[i - 1][2].get("to") or evs[i - 1][1]  # 前一事件后所处的状态
            t1, t0 = _parse_ts(evs[i][0]), _parse_ts(evs[i - 1][0])
            if t1 is None or t0 is None:
                continue
            dt = t1 - t0
            if frm in STATUSES and dt >= 0:
                spans.setdefault(frm, []).append(dt / 3600)
    return {s: round(sum(v) / len(v), 1) for s, v in spans.items() if v}


# ---------- 写 ----------

def create(title, body="", labels=None, priority="normal", due=None, color=None, actor="webui") -> dict:
    title = re.sub(r"[\n\r]", " ", title or "").strip()
    if not title:
        raise ApiError(400, "标题不能为空")
    if priority not in PRIORITIES:
        raise ApiError(400, "非法 priority")
    _check_due(due)
    labels = _norm_labels(labels)
    ts = now()
    with _lock:
        c = db.conn()
        with c:
            stem = f"{time.strftime('%Y%m%d')}-{slugify(title)}"
            cid, n = stem, 2
            while c.execute("SELECT 1 FROM tasks WHERE id=?", (cid,)).fetchone():
                cid, n = f"{stem}-{n}", n + 1
            pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM tasks "
                            "WHERE board_id=1 AND status='待执行'").fetchone()[0]
            c.execute("INSERT INTO tasks(id, board_id, title, status, owner, priority, labels, color, "
                      "due, body, position, created, updated) VALUES(?,1,?,'待执行','',?,?,?,?,?,?,?,?)",
                      (cid, title, priority, json.dumps(labels, ensure_ascii=False), color, due,
                       (body or "").strip(), pos, ts, ts))
            _ev(c, cid, "created", {"title": title}, actor)
            db.bump_rev(c)
        loaded = _load_for_mirror(c, cid)
    _after_write(cid, loaded)
    return detail(cid)


def patch(cid, actor="webui", **f) -> dict:
    with _lock:
        c = db.conn()
        with c:
            _get(c, cid)
            sets, evs = {}, []
            if f.get("title") is not None:
                t = re.sub(r"[\n\r]", " ", f["title"]).strip()
                if not t:
                    raise ApiError(400, "标题不能为空")
                sets["title"], _ = t, evs.append(("title", {"to": t}))
            if f.get("owner") is not None:
                o = _norm_owner(f["owner"])
                sets["owner"], _ = o, evs.append(("owner", {"to": o}))
            if f.get("body") is not None:
                sets["body"] = f["body"]
                evs.append(("body", {}))
            if f.get("labels") is not None:
                sets["labels"] = json.dumps(_norm_labels(f["labels"]), ensure_ascii=False)
                evs.append(("labels", {"to": _norm_labels(f["labels"])}))
            if f.get("priority") is not None:
                if f["priority"] not in PRIORITIES:
                    raise ApiError(400, "非法 priority")
                sets["priority"], _ = f["priority"], evs.append(("priority", {"to": f["priority"]}))
            if f.get("due") is not None:
                _check_due(f["due"])
                sets["due"], _ = f["due"], evs.append(("due", {"to": f["due"]}))
            if f.get("color") is not None:
                sets["color"], _ = (f["color"] or None), evs.append(("color", {"to": f["color"]}))
            if not sets:
                pass  # 空补丁: 不动
            else:
                sets["updated"] = now()
                cols = ", ".join(f"{k}=?" for k in sets)
                c.execute(f"UPDATE tasks SET {cols} WHERE id=?", (*sets.values(), cid))
                for name, d in evs:
                    _ev(c, cid, name, d, actor)
                db.bump_rev(c)
        loaded = _load_for_mirror(c, cid)
    _after_write(cid, loaded)
    return detail(cid)


def move(cid, status, before_id=None, actor="webui") -> dict:
    if status not in STATUSES:
        raise ApiError(400, "非法 status")
    with _lock:
        c = db.conn()
        with c:
            row = _get(c, cid)
            old = row["status"]
            col = [r["id"] for r in c.execute(
                "SELECT id FROM tasks WHERE board_id=1 AND status=? ORDER BY position, created",
                (status,)) if r["id"] != cid]
            if before_id is not None and before_id != cid:
                if before_id not in col:
                    raise ApiError(400, "before_id 不在目标列")
                col.insert(col.index(before_id), cid)
            else:
                col.append(cid)
            for i, tid in enumerate(col):
                c.execute("UPDATE tasks SET position=? WHERE id=?", (i, tid))
            if old != status:
                c.execute("UPDATE tasks SET status=?, updated=? WHERE id=?", (status, now(), cid))
                _ev(c, cid, "status", {"from": old, "to": status}, actor)
            db.bump_rev(c)  # 纯重排也要刷新页面
        loaded = _load_for_mirror(c, cid)
    _after_write(cid, loaded)
    return detail(cid)


def take(owner, cid=None, actor=None) -> dict:
    owner = _norm_owner(owner)
    if not owner:
        raise ApiError(400, "owner 不能为空")
    actor = actor or owner
    with _lock:
        c = db.conn()
        with c:
            if cid:
                row = _get(c, cid)
            else:
                row = c.execute("SELECT * FROM tasks WHERE board_id=1 AND status='待执行' AND owner='' "
                                "ORDER BY created, position LIMIT 1").fetchone()
                if not row:
                    raise ApiError(404, "没有可认领的待执行卡")
            if row["status"] == "完成":
                raise ApiError(400, "已归档, 不能认领")
            if row["owner"] and row["owner"] != owner:
                raise ApiError(409, f"已被 {row['owner']} 认领")
            if row["owner"] == owner and row["status"] == "执行中":
                return detail(row["id"])  # 自己已持有的卡: 幂等, 不重复记事件
            if not row["owner"] and row["status"] != "待执行":
                raise ApiError(400, f"状态为 {row['status']}, 不能认领")
            cur = c.execute("UPDATE tasks SET owner=?, status='执行中', updated=? WHERE id=? "
                            "AND (owner='' OR owner=?) AND status!='完成'", (owner, now(), row["id"], owner))
            if cur.rowcount == 0:
                raise ApiError(409, "认领冲突: 刚被其他会话取走")
            _ev(c, row["id"], "take", {"owner": owner}, actor)
            db.bump_rev(c)
        loaded = _load_for_mirror(c, row["id"])
    _after_write(row["id"], loaded)
    return detail(row["id"])


def add_log(cid, text, actor="webui") -> dict:
    text = " ".join((text or "").split())[:300]
    if not text:
        raise ApiError(400, "日志不能为空")
    with _lock:
        c = db.conn()
        with c:
            _get(c, cid)
            c.execute("UPDATE tasks SET updated=? WHERE id=?", (now(), cid))
            _ev(c, cid, "log", {"text": text}, actor)
            db.bump_rev(c)
        loaded = _load_for_mirror(c, cid)
    _after_write(cid, loaded)
    return detail(cid)


def restore(cid, actor="webui") -> dict:
    with _lock:
        c = db.conn()
        with c:
            row = _get(c, cid)
            if row["status"] != "完成":
                raise ApiError(400, "仅完成卡可恢复")
            pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM tasks "
                            "WHERE board_id=1 AND status='待执行'").fetchone()[0]
            c.execute("UPDATE tasks SET status='待执行', position=?, updated=? WHERE id=?",
                      (pos, now(), cid))
            _ev(c, cid, "restore", {}, actor)
            db.bump_rev(c)
        loaded = _load_for_mirror(c, cid)
    _after_write(cid, loaded)
    return detail(cid)


def delete(cid, actor="webui") -> dict:
    with _lock:
        c = db.conn()
        with c:
            _get(c, cid)
            loaded = _load_for_mirror(c, cid)
            c.execute("DELETE FROM task_events WHERE task_id=?", (cid,))
            c.execute("DELETE FROM tasks WHERE id=?", (cid,))
            db.bump_rev(c)
    mirror.trash(*loaded)
    db.maybe_backup()
    return {"ok": True, "id": cid}


# ---------- 旧系统导入 (migrate_md 调用) ----------

def import_task(cid, title, status, owner, created, updated, body, events, actor="旧卡导入") -> bool:
    """幂等导入: 已存在同 id 返回 False。events=[(ts, actor, event, detail)]。"""
    if status not in STATUSES:
        status = "待执行"
    with _lock:
        c = db.conn()
        with c:
            cur = c.execute(
                "INSERT OR IGNORE INTO tasks(id, board_id, title, status, owner, priority, labels, "
                "color, due, body, position, created, updated) "
                "VALUES(?,1,?,?,'','normal','[]',NULL,NULL,?,0,?,?)",
                (cid, title, status, (body or "").strip(), created or now(), updated or now()))
            if cur.rowcount == 0:
                return False
            c.execute("UPDATE tasks SET owner=? WHERE id=?", (_norm_owner(owner), cid))
            for ts, a, e, d in events:
                c.execute("INSERT INTO task_events(task_id, ts, actor, event, detail) VALUES(?,?,?,?,?)",
                          (cid, ts, (a or actor)[:40], e, json.dumps(d, ensure_ascii=False)))
            _ev(c, cid, "imported", {"from": "task-board"}, actor)
            db.bump_rev(c)
        loaded = _load_for_mirror(c, cid)
    _after_write(cid, loaded)
    return True
