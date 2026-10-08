"""REST API: CRUD/move/restore/delete/鉴权/SSE。"""
import time

import pytest
from fastapi.testclient import TestClient

from app import config, service
from app.api import _rev_stream, create_app


@pytest.fixture()
def client(home):
    with TestClient(create_app()) as c:
        yield c


def _mk(client, title, **kw):
    r = client.post("/api/tasks", json={"title": title, **kw})
    assert r.status_code == 201, r.text
    return r.json()["task"]


def test_crud_flow(client, home):
    t = _mk(client, "调研看板")
    cid = t["id"]
    assert cid.startswith("2026") and "调研看板" in cid
    assert client.get("/api/board").json()["tasks"][0]["status"] == "待执行"

    r = client.patch(f"/api/tasks/{cid}", json={"title": "调研看板项目", "priority": "high"})
    assert r.json()["task"]["title"] == "调研看板项目"
    assert r.json()["task"]["priority"] == "high"

    r = client.post(f"/api/tasks/{cid}/log", json={"text": "开工", "actor": "bot"})
    assert any(e["event"] == "log" for e in r.json()["events"])

    for st in ("执行中", "待验收", "完成"):
        r = client.post(f"/api/tasks/{cid}/move", json={"status": st})
        assert r.json()["task"]["status"] == st
    b = client.get("/api/board").json()
    assert b["tasks"] == [] and b["done"][0]["id"] == cid

    assert client.post(f"/api/tasks/{cid}/restore").json()["task"]["status"] == "待执行"
    assert client.delete(f"/api/tasks/{cid}").status_code == 200
    assert client.get(f"/api/tasks/{cid}").status_code == 404


def test_move_before_id(client):
    a, b, c = (_mk(client, x)["id"] for x in ("甲", "乙", "丙"))
    client.post(f"/api/tasks/{c}/move", json={"status": "待执行", "before_id": a})
    ids = [t["id"] for t in client.get("/api/board").json()["tasks"]]
    assert ids == [c, a, b]
    # 跨列拖拽: 移到执行中列头
    client.post(f"/api/tasks/{a}/move", json={"status": "执行中", "before_id": None})
    b2 = client.get("/api/board").json()["tasks"]
    assert [t["id"] for t in b2 if t["status"] == "执行中"] == [a]
    assert client.post(f"/api/tasks/{a}/move", json={"status": "坏状态"}).status_code == 400


def test_take_atomic(home):
    t = service.create("可抢卡")["task"]
    assert service.take("w1", t["id"])["task"]["owner"] == "w1"
    with pytest.raises(service.ApiError) as e:
        service.take("w2", t["id"])
    assert e.value.code == 409
    # 再认领自己的卡: 幂等
    assert service.take("w1", t["id"])["task"]["owner"] == "w1"


def test_take_auto_pick(home):
    a = service.create("旧卡")["task"]
    service.create("新卡")
    got = service.take("worker")["task"]
    assert got["id"] == a["id"] and got["status"] == "执行中"
    # 自动捞按创建序: 第二次捞到第二张, 之后无卡可捞 404
    assert service.take("worker2")["task"]["title"] == "新卡"
    with pytest.raises(service.ApiError) as e:
        service.take("worker3")
    assert e.value.code == 404


def test_auth(home, monkeypatch):
    monkeypatch.setattr(config, "TOKEN", "s3cret")
    with TestClient(create_app()) as c:
        assert c.get("/api/board").status_code == 401
        assert c.get("/api/board?token=s3cret").status_code == 200
        assert c.get("/api/board", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/board", headers={"Authorization": "Bearer bad"}).status_code == 401


def test_sse_rev(home):
    # 有界测试: 假 request 第二次探活即断连, 生成器发一行后停止(不走无限流端点, 那会挂 TestClient)
    import asyncio

    from app import db

    class _Req:
        n = 0

        async def is_disconnected(self):
            self.n += 1
            return self.n > 1

    lines = asyncio.run(_collect(_Req()))
    assert lines == [f"data: {db.get_rev()}\n\n"]


async def _collect(req):
    return [ln async for ln in _rev_stream(req)]


def test_insights(client, home):
    from app import db
    t1 = _mk(client, "停滞卡")
    t2 = _mk(client, "滞留验收卡")
    _mk(client, "临期卡", due=time.strftime("%Y-%m-%d", time.localtime(time.time() + 86400)))
    _mk(client, "逾期卡", due="2026-01-01")
    # 摆状态 + 把 updated 拨回 5h/30h 前
    client.post(f"/api/tasks/{t1['id']}/move", json={"status": "执行中"})
    client.post(f"/api/tasks/{t2['id']}/move", json={"status": "待验收"})
    c = db.conn()
    with c:
        c.execute("UPDATE tasks SET updated=datetime('now','localtime','-5 hours') WHERE title='停滞卡'")
        c.execute("UPDATE tasks SET updated=datetime('now','localtime','-30 hours') WHERE title='滞留验收卡'")
    i = client.get("/api/insights").json()
    assert i["counts"]["待执行"] == 2
    assert any(t["title"] == "停滞卡" and t["hours"] >= 5 for t in i["stalled"])
    assert any(t["title"] == "滞留验收卡" for t in i["review_overdue"])
    assert {t["title"] for t in i["due_soon"]} == {"临期卡"}
    assert {t["title"] for t in i["overdue"]} == {"逾期卡"}
    assert any("停滞" in s for s in i["suggestions"])
