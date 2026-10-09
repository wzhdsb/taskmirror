"""任务节点进度条(checkbox steps 现算) + 短代号 code(分配/回填/按代号捞卡)。"""

import time

from fastapi.testclient import TestClient

from app import service
from app.api import create_app


def _mk(c, title, **kw):
    return c.post("/api/tasks", json={"title": title, **kw}).json()["task"]["id"]


def test_steps_parse(home):
    with TestClient(create_app()) as c:
        tid = _mk(c, "节点卡", body="## ③ 交付物\n- [ ] 拆解节点\n- [x] 已完成项\n- [X] 大写容忍\n"
                                    "行内 [x] 不算, 普通列表:\n- 普通项\n* [ ] 星号列表也算")
        t = c.get(f"/api/tasks/{tid}").json()["task"]
        assert t["stepsTotal"] == 4 and t["stepsDone"] == 2  # 行内/普通列表不计
        # board 同样现算
        by = {x["id"]: x for x in c.get("/api/board").json()["tasks"]}
        assert by[tid]["stepsTotal"] == 4
        # 勾一个 → steps 涨(body patch 即触发)
        c.patch(f"/api/tasks/{tid}", json={"body": t["body"].replace("- [ ] 拆解节点", "- [x] 拆解节点")})
        t2 = c.get(f"/api/tasks/{tid}").json()["task"]
        assert t2["stepsDone"] == 3 and t2["stepsTotal"] == 4
        # 无 checkbox → 0, 前端不渲染
        t3 = _mk(c, "无节点卡")
        assert c.get(f"/api/tasks/{t3}").json()["task"]["stepsTotal"] == 0


def test_code_assign_and_take(home):
    with TestClient(create_app()) as c:
        mmdd = time.strftime("%m%d")
        a = _mk(c, "代号A")
        b = _mk(c, "代号B")
        ta, tb = (c.get(f"/api/tasks/{x}").json()["task"] for x in (a, b))
        assert ta["code"] == f"{mmdd}-1" and tb["code"] == f"{mmdd}-2"
        # 归档后序号不复用: done 的 code 也计入当日最大
        done_id = _mk(c, "代号C")
        c.post(f"/api/tasks/{done_id}/move", json={"status": "完成", "before_id": None})
        d = _mk(c, "代号D")
        assert c.get(f"/api/tasks/{d}").json()["task"]["code"] == f"{mmdd}-4"
        # 按短代号捞卡 → 解析到真 id
        r = service.take("executor", tb["code"])
        assert r["task"]["id"] == b and r["task"]["owner"] == "executor"
        # 完整 id 优先: 存在以代号同形的 id 时不被劫持
        try:
            service.take("executor", "9999-9")
            assert False
        except service.ApiError as e:
            assert e.code == 404


def test_code_backfill_migration(home):
    """v4 回填: 存量 code='' 按 created 日 ROW_NUMBER 补齐(直接跑 MIGRATIONS[4] 的 SQL)。"""
    from app import db
    with TestClient(create_app()) as c:
        a = _mk(c, "回填A")
        b = _mk(c, "回填B")
        with db.conn() as conn:
            conn.execute("UPDATE tasks SET code=''")  # 模拟存量无代号
            conn.execute(db.MIGRATIONS[4][1])         # v4 的回填 UPDATE 原文
            rows = conn.execute("SELECT id, code FROM tasks").fetchall()
        by = {r["id"]: r["code"] for r in rows}
        mmdd = time.strftime("%m%d")
        assert by[a] == f"{mmdd}-1" and by[b] == f"{mmdd}-2"
