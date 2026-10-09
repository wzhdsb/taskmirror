"""全部业务逻辑: task CRUD / move / take / log + task_events 追加 + rev + 镜像触发。
Web(api.py) 与 MCP(mcp_server.py) 两个进程共用本模块, 各自直写同一 SQLite(WAL)。
写路径: 进程内锁串行 + 短事务; 跨进程靠 busy_timeout(5s), 单机两进程远达不到瓶颈。
ponytail: 若未来出现写冲突 SQLITE_BUSY, 在此加一次重试即可, 不需要队列。"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from . import config, db, mirror

STATUSES = ("待执行", "执行中", "待验收", "完成")
PRIORITIES = ("low", "normal", "high", "urgent")
STATUS_ORD = {s: i for i, s in enumerate(STATUSES)}
LOG_LINE_RE = re.compile(r"^- (\d{2}-\d{2} \d{2}:\d{2}) \[(.+?)\] (.*)$")
CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\] ", re.MULTILINE)  # 任务节点: 正文 checkbox 行(行内[x]与普通列表不算)
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
    for k in ("labels", "related", "depends"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except ValueError:
            d[k] = []
    boxes = CHECKBOX_RE.findall(d.get("body") or "")
    d["stepsDone"], d["stepsTotal"] = sum(b != " " for b in boxes), len(boxes)
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

def _annotate(rows: list[dict]) -> None:
    """关联反查现算(不落盘): relatesBack/dependedBy/waiting。
    ponytail: 每请求 O(n²) 全表扫, 卡过千再考虑缓存/触发器。"""
    by_id = {t["id"]: t for t in rows}
    for t in rows:
        t["relatesBack"] = [r["id"] for r in rows if t["id"] in r["related"]]
        t["dependedBy"] = [r["id"] for r in rows if t["id"] in r["depends"]]
        t["waiting"] = [d for d in t["depends"]
                        if d in by_id and by_id[d]["status"] != "完成"]


def board() -> dict:
    c = db.conn()
    rows = [_serialize(r) for r in c.execute(
        "SELECT * FROM tasks WHERE board_id=1 ORDER BY position, created")]
    _annotate(rows)
    tasks = [t for t in rows if t["status"] != "完成"]
    # 待执行列按 waiting 数沉底: 被前置阻塞的卡不挡道
    tasks.sort(key=lambda t: (STATUS_ORD.get(t["status"], 9),
                              len(t["waiting"]) if t["status"] == "待执行" else 0,
                              t["position"]))
    done = sorted((t for t in rows if t["status"] == "完成"),
                  key=lambda t: t["updated"], reverse=True)[:50]
    return {"tasks": tasks, "done": done, "rev": db.get_rev()}


def detail(cid: str) -> dict:
    c = db.conn()
    task, evs = _load_for_mirror(c, cid)
    rows = [_serialize(r) for r in c.execute("SELECT * FROM tasks WHERE board_id=1")]
    _annotate(rows)  # drawer 关联区要反查边; O(n) 单次, 卡过千再瘦身
    task = next((r for r in rows if r["id"] == cid), task)
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
    # 被未完成前置阻塞的待执行卡(自闭环捞卡会自动跳过, 这里提示拆卡人)
    st = {t["id"]: t["status"] for t in rows}
    blocked = [t for t in rows if t["status"] == "待执行"
               and [d for d in t["depends"] if st.get(d) not in (None, "完成")]]
    if blocked:
        suggestions.append(f"{len(blocked)} 张待执行卡被未完成前置阻塞: "
                           + ", ".join(t["id"] for t in blocked) + " — 先推进前置或摘除依赖")
    if review_overdue:
        suggestions.append(f"{len(review_overdue)} 张卡待验收滞留超 {REVIEW_HOURS}h, 请验收或打回")
    # AI 失联: executor 持有但进程不在(崩溃/被杀/服务重启丢账), >5min 无动静
    ai_lost = [t for t in rows if t["status"] == "执行中" and t["owner"] == "executor"
               and t["id"] not in DISPATCHES and _age_hours(t["updated"]) > 5 / 60]
    if ai_lost:
        suggestions.append(f"{len(ai_lost)} 张卡 AI 已失联(进程不在): "
                           + ", ".join(t["id"] for t in ai_lost) + " — 打回待执行或重派")
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
            # 短代号 MMDD-N: 当日(含归档)全局最大序号+1, 持锁分配不并发撞号
            mmdd = time.strftime("%m%d")
            nseq = 1 + max((int(r[0].rsplit("-", 1)[-1]) for r in c.execute(
                "SELECT code FROM tasks WHERE code LIKE ?", (mmdd + "-%",))
                if r[0].rsplit("-", 1)[-1].isdigit()), default=0)
            pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM tasks "
                            "WHERE board_id=1 AND status='待执行'").fetchone()[0]
            c.execute("INSERT INTO tasks(id, board_id, title, status, owner, priority, labels, color, "
                      "due, body, position, created, updated, code) VALUES(?,1,?,'待执行','',?,?,?,?,?,?,?,?,?)",
                      (cid, title, priority, json.dumps(labels, ensure_ascii=False), color, due,
                       (body or "").strip() or "## 需求", pos, ts, ts, f"{mmdd}-{nseq}"))
            _ev(c, cid, "created", {"title": title}, actor)
            db.bump_rev(c)
        loaded = _load_for_mirror(c, cid)
    _after_write(cid, loaded)
    return detail(cid)


def patch(cid, actor=None, **f) -> dict:
    """actor 缺省回落卡 owner: MCP 调用方即卡主; REST(api.py)显式传 webui。"""
    with _lock:
        c = db.conn()
        with c:
            row = _get(c, cid)
            if row["status"] == "完成" and (f.get("owner") is not None or f.get("body") is not None
                                            or f.get("labels") is not None):
                raise ApiError(400, "已归档, 仅可重开(移回前四态)")
            actor = actor or row["owner"] or "webui"
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


def move(cid, status, before_id=None, actor=None) -> dict:
    if status not in STATUSES:
        raise ApiError(400, "非法 status")
    with _lock:
        c = db.conn()
        with c:
            row = _get(c, cid)
            actor = actor or row["owner"] or "webui"
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
    if status == "待验收" and old != status:
        _auto_review(cid)  # 自动验收钩子: REST/MCP/拖卡都汇到 move(), 一处全覆盖
    return detail(cid)


def _blocked(c: sqlite3.Connection, row: sqlite3.Row) -> list[str]:
    """depends 中活跃且未完成的前置 id(不存在的 id 视为已消化, 不阻塞)。"""
    deps = json.loads(row["depends"] or "[]")
    return [d for d in deps if (r := c.execute("SELECT status FROM tasks WHERE id=?", (d,)).fetchone())
            and r["status"] != "完成"]


def take(owner, cid=None, actor=None) -> dict:
    owner = _norm_owner(owner)
    if not owner:
        raise ApiError(400, "owner 不能为空")
    actor = actor or owner
    with _lock:
        c = db.conn()
        with c:
            if cid:
                # 短代号定位: 口头/会话引用 MMDD-N, 无歧义时解析到真 id
                if not c.execute("SELECT 1 FROM tasks WHERE id=?", (cid,)).fetchone():
                    hit = c.execute("SELECT id FROM tasks WHERE code=?", (cid,)).fetchall()
                    if len(hit) == 1:
                        cid = hit[0]["id"]
                row = _get(c, cid)
                blocked = _blocked(c, row)
                if blocked:
                    raise ApiError(409, f"有未完成前置: {', '.join(blocked)} — 先推进前置或摘除依赖")
            else:
                # 自闭环协议: 被前置阻塞的卡自动跳过换下一张, 不停下问人
                row = None
                for cand in c.execute(
                        "SELECT * FROM tasks WHERE board_id=1 AND status='待执行' AND owner='' "
                        "ORDER BY created, position"):
                    if not _blocked(c, cand):
                        row = cand
                        break
                if not row:
                    raise ApiError(404, "没有可认领的待执行卡(或全部被未完成前置阻塞)")
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


def relate(cid, target, kind="related", remove=False, actor=None) -> dict:
    """关联维护, 单向声明只写发起方: related=同源配套, depends=我的前置。
    幂等(重复添加/移除不存在的都不报错); 目标可为归档卡; 归档卡发起一律拒绝。"""
    if kind not in ("related", "depends"):
        raise ApiError(400, "kind 需为 related|depends")
    with _lock:
        c = db.conn()
        with c:
            row = _get(c, cid)
            if cid == target:
                raise ApiError(400, "不能关联自己")
            if not c.execute("SELECT 1 FROM tasks WHERE id=?", (target,)).fetchone():
                raise ApiError(404, f"目标卡 {target} 不存在")
            if row["status"] == "完成":
                raise ApiError(400, "已归档, 仅可重开(移回前四态)")
            if kind == "depends" and not remove:  # 环防护: target 沿 depends 链走回 cid 即拒
                dep = {r[0]: json.loads(r[1]) for r in c.execute("SELECT id, depends FROM tasks")}
                seen, stack = {target}, [target]
                while stack:
                    for nxt in dep.get(stack.pop(), []):
                        if nxt == cid:
                            raise ApiError(400, f"会成环: {target} 已(间接)依赖 {cid}")
                        if nxt in dep and nxt not in seen:
                            seen.add(nxt)
                            stack.append(nxt)
            cur = _serialize(row)[kind]
            new = ([x for x in cur if x != target] if remove
                   else (cur + [target] if target not in cur else cur))
            if new != cur:
                actor = actor or row["owner"] or "webui"
                c.execute(f"UPDATE tasks SET {kind}=?, updated=? WHERE id=?",
                          (json.dumps(new, ensure_ascii=False), now(), cid))
                _ev(c, cid, "relate", {"kind": kind, "target": target, "removed": remove}, actor)
                db.bump_rev(c)
        loaded = _load_for_mirror(c, cid)
    _after_write(cid, loaded)
    return detail(cid)


def add_log(cid, text, actor=None) -> dict:
    text = " ".join((text or "").split())[:300]
    if not text:
        raise ApiError(400, "日志不能为空")
    with _lock:
        c = db.conn()
        with c:
            row = _get(c, cid)
            actor = actor or row["owner"] or "webui"
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


# ---------- 网页派发: claude -p + task-executor agent 的进程管理 ----------
# DISPATCHES 为运行时视图(含 Popen/句柄), dispatches 表为持久真源: 服务重启后据此恢复/清孤儿。
# 全权模式(--dangerously-skip-permissions)已经用户批准(2026-10-09)。

DISPATCHES: dict[str, dict] = {}
MAX_DISPATCH = 4  # 对齐 /dispatch 并发预算


def system_event(cid: str, event: str, detail: dict, actor: str = "system") -> None:
    """系统级事件(派发/停止/失联)进事件流, 让网页"看得见"。"""
    with _lock:
        c = db.conn()
        with c:
            _ev(c, cid, event, detail, actor)
            db.bump_rev(c)


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            k32.CloseHandle(h)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_tree(pid: int) -> None:
    if os.name == "nt":  # cmd 包装进程树, 单 terminate 杀不干净
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                       capture_output=True, check=False)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


class _AdoptedProc:
    """服务重启后收养的进程: 没有 Popen 句柄, 按 pid 活性模拟 poll。"""

    def __init__(self, pid: int):
        self.pid = pid

    def poll(self):
        return None if _pid_alive(self.pid) else 0

    def terminate(self):
        _kill_tree(self.pid)


def _reap() -> None:
    for cid, d in list(DISPATCHES.items()):
        rc = d["proc"].poll()
        if rc is not None:
            d["log_fh"].close()
            del DISPATCHES[cid]
            c = db.conn()
            with c:
                c.execute("DELETE FROM dispatches WHERE task_id=?", (cid,))
                db.bump_rev(c)
            system_event(cid, "dispatch_exit", {"pid": d["pid"], "rc": rc})


def resume_dispatches() -> None:
    """启动时对账 dispatches 表: 活进程收养, 死进程清账+失联事件。"""
    c = db.conn()
    for row in c.execute("SELECT * FROM dispatches").fetchall():
        cid, pid = row["task_id"], row["pid"]
        if _pid_alive(pid):
            fh = open(row["log"], "a", encoding="utf-8")  # noqa: SIM115 随进程存续, reaped/stop 时关闭
            DISPATCHES[cid] = {"pid": pid, "started": row["started"],
                               "proc": _AdoptedProc(pid), "log_fh": fh, "log": row["log"]}
        else:
            with c:
                c.execute("DELETE FROM dispatches WHERE task_id=?", (cid,))
                db.bump_rev(c)
            system_event(cid, "dispatch_lost", {"pid": pid})


def _launch(cid: str, agent: str, prompt: str, ev: str, actor: str) -> dict:
    """spawn claude -p 子代理, dispatch(执行)/review(验收) 共用进程管理与并发预算。"""
    if cid in DISPATCHES:
        raise ApiError(409, "该卡已有 AI 进程")
    if len(DISPATCHES) >= MAX_DISPATCH:
        raise ApiError(429, f"AI 并发已满({MAX_DISPATCH})，稍后再试")
    claude = shutil.which("claude")
    if not claude:
        raise ApiError(500, "PATH 里未找到 claude 命令")
    logs = config.home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = str(logs / f"dispatch-{cid}-{int(time.time())}.jsonl")
    # stream-json: 每行一个事件(assistant/tool_use/result), activity() 解析成网页过程流
    lf = open(log, "w", encoding="utf-8")  # noqa: SIM115 随进程存续, reaped/stop 时关闭
    proc = subprocess.Popen(
        [claude, "-p", "--agent", agent, "--dangerously-skip-permissions",
         "--output-format", "stream-json", "--verbose", prompt],
        stdout=lf, stderr=subprocess.STDOUT, cwd=str(config.repo_root()),
        env={**os.environ, "PYTHONUTF8": "1"},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    DISPATCHES[cid] = {"pid": proc.pid, "started": started, "proc": proc, "log_fh": lf, "log": log}
    c = db.conn()
    with c:
        c.execute("INSERT OR REPLACE INTO dispatches(task_id, pid, log, started) VALUES(?,?,?,?)",
                  (cid, proc.pid, log, started))
        db.bump_rev(c)
    system_event(cid, ev, {"pid": proc.pid, "log": Path(log).name}, actor)
    return {"ok": True, "pid": proc.pid}


def dispatch(cid: str, actor: str = "webui") -> dict:
    _reap()
    t = _get(db.conn(), cid)
    if t["status"] != "待执行":
        raise ApiError(400, "仅待执行卡可派发")
    prompt = (f"从 TaskMirror 用 take_task 认领任务卡 {cid}，按卡内四段式(①任务②已知事实③交付物④注意)"
              f"执行到待验收，进展用 add_log 追加。")
    return _launch(cid, "task-executor", prompt, "dispatched", actor)


REVIEW_MAX = 2  # 每卡自动验收次数上限: 打回→重做→再验 之后转人工, 防拉锯


def review(cid: str, actor: str = "webui") -> dict:
    """派验收 agent 异步核验待验收卡(复用 dispatch 进程管理/活动流/停止按钮)。"""
    _reap()
    if _get(db.conn(), cid)["status"] != "待验收":
        raise ApiError(400, "仅待验收卡可验收")
    prompt = (f"验收 TaskMirror 任务卡 {cid}: get_task 读卡, 对③交付物逐项实证核验(重跑卡内验收命令/"
              f"查文件/跑测试, 不信执行者日志自述)。三判一: ①通过=add_log 写验收报告后 set_status 完成;"
              f" ②打回=set_status 待执行+add_log 具体缺口; ③需人工(发版/对外/视觉观感/业务取舍/新量化数字)"
              f"=update_task 加标签「人工验收」+add_log 说明。不改任何代码, 不补交付物。")
    return _launch(cid, "task-acceptor", prompt, "review", actor)


def _auto_review(cid: str) -> None:
    """卡到待验收即自动派验收 agent(真异步: 服务常驻, 主会话不在也跑)。
    TASKBOARD_REVIEWER 开关(默认关, 测试/本地免惊扰); 「人工验收」label 与每卡次数上限拦住。"""
    if os.environ.get("TASKBOARD_REVIEWER") != "1":
        return
    c = db.conn()
    row = _get(c, cid)
    if "人工验收" in json.loads(row["labels"] or "[]"):
        return
    n = c.execute("SELECT COUNT(*) FROM task_events WHERE task_id=? AND event='review'",
                  (cid,)).fetchone()[0]
    if n >= REVIEW_MAX:
        system_event(cid, "review_skip", {"reason": f"自动验收已达 {REVIEW_MAX} 次上限, 转人工"})
        return
    try:
        review(cid, actor="auto-reviewer")
    except ApiError as e:
        system_event(cid, "review_skip", {"reason": e.msg})


def stop_dispatch(cid: str, actor: str = "webui") -> dict:
    _reap()
    d = DISPATCHES.get(cid)
    if not d:
        raise ApiError(404, "该卡无进行中的 AI 进程")
    _kill_tree(d["pid"])
    d["log_fh"].close()
    del DISPATCHES[cid]
    c = db.conn()
    with c:
        c.execute("DELETE FROM dispatches WHERE task_id=?", (cid,))
        db.bump_rev(c)
    system_event(cid, "dispatch_stopped", {"pid": d["pid"]}, actor)
    if _get(db.conn(), cid)["status"] == "待验收":
        return {"ok": True}  # 验收进程或已交付的执行进程: 只杀不打回, 交付不毁
    # 进程已死, 卡不可能再推进: 打回待执行清 owner, 供重新派发/人工接手
    patch(cid, owner="", actor="system")
    move(cid, "待执行", None, actor="system")
    return {"ok": True}


def recent_dispatches(limit: int = 5) -> list[dict]:
    """最近已结束的派发日志(监控页回看用); 正在跑的不重复列。"""
    p = config.home() / "logs"
    hits = sorted((x for x in p.glob("dispatch-*.jsonl") if p.exists()),
                  key=os.path.getmtime, reverse=True) if p.exists() else []
    out = []
    for h in hits:
        cid = h.name[len("dispatch-"):].rsplit("-", 1)[0]
        if cid in DISPATCHES:
            continue
        out.append({"task_id": cid, "ended": time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(h)))})
        if len(out) >= limit:
            break
    return out


def running() -> list[dict]:
    _reap()
    out = []
    for k, v in DISPATCHES.items():
        # agent 类型从最近 dispatched/review 事件派生(不加表列, 重启收养后同样成立); 并发≤4 小查询
        ev = db.conn().execute(
            "SELECT event FROM task_events WHERE task_id=? AND event IN('dispatched','review') "
            "ORDER BY id DESC LIMIT 1", (k,)).fetchone()
        out.append({"task_id": k, "pid": v["pid"], "started": v["started"],
                    "agent": "acceptor" if ev and ev["event"] == "review" else "executor"})
    return out


def _parse_activity(lines: list[str]) -> list[dict]:
    """stream-json 行 → 人读活动项(借鉴 zcode ToolCallBlock: 摘要优先, 不淹没在 JSON 里)。"""
    out = []
    for ln in lines:
        try:
            ev = json.loads(ln)
        except (ValueError, TypeError):
            continue
        ts = ""
        raw_ts = ev.get("timestamp")
        if raw_ts:
            try:
                ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")) \
                         .astimezone().strftime("%H:%M:%S")
            except ValueError:
                pass
        if ev.get("type") == "assistant":
            for blk in ev.get("message", {}).get("content", []):
                if blk.get("type") == "tool_use":
                    name = blk.get("name", "?").split("__")[-1]
                    arg = next((str(v) for v in (blk.get("input") or {}).values()
                                if isinstance(v, str) and v.strip()), "")
                    out.append({"kind": "tool", "text": f"{name}({arg[:44]})", "ts": ts})
                elif blk.get("type") == "text" and blk.get("text", "").strip():
                    out.append({"kind": "text", "text": blk["text"].strip()[:110], "ts": ts})
        elif ev.get("type") == "result":
            out.append({"kind": "done", "text": str(ev.get("result", ""))[:130], "ts": ts})
    return out[-30:]


def activity(cid: str, raw: bool = False) -> dict:
    """运行中取 DISPATCHES 的 log; 已结束取最近一次日志(过程可回看)。
    raw=True 回进程真实 stdout 尾行(每行截 400 字, 终端原文视图); 默认解析成人读摘要。"""
    d = DISPATCHES.get(cid)
    if d:
        log = d["log"]
    else:
        p = config.home() / "logs"
        hits = sorted(p.glob(f"dispatch-{cid}-*.jsonl")) if p.exists() else []
        log = str(hits[-1]) if hits else None
    if not log or not os.path.isfile(log):
        return {"activities": [], "raw": [], "running": bool(d)}
    with open(log, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()[-400:]
    if raw:
        return {"raw": [ln.rstrip()[:400] for ln in lines[-200:]], "running": bool(d)}
    return {"activities": _parse_activity(lines), "running": bool(d)}
