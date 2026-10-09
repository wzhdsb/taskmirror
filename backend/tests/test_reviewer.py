"""自动验收: review() 派验收 agent + move→待验收 钩子(开关/人工验收 label/每卡上限)。"""

from fastapi.testclient import TestClient

from app import service
from app.api import create_app


def _mk(c, title, **kw):
    return c.post("/api/tasks", json={"title": title, **kw}).json()["task"]["id"]


def _fake_launch(fired):
    def launch(cid, agent, prompt, ev, actor="webui"):
        fired.append((cid, agent))
        service.system_event(cid, ev, {"pid": 1}, actor)  # 真实 _launch 的事件副作用, 供上限计数
        return {"ok": True, "pid": 1}
    return launch


def test_review_endpoint_guard(home, monkeypatch):
    fired = []
    monkeypatch.setattr(service, "_launch", _fake_launch(fired))
    with TestClient(create_app()) as c:
        tid = _mk(c, "验收卡")
        assert c.post(f"/api/tasks/{tid}/review").status_code == 400  # 非待验收不可派
        c.post(f"/api/tasks/{tid}/move", json={"status": "待验收"})
        assert c.post(f"/api/tasks/{tid}/review").json()["ok"] is True
        assert fired == [(tid, "task-acceptor")]


def test_auto_review_hook(home, monkeypatch):
    fired = []
    monkeypatch.setattr(service, "_launch", _fake_launch(fired))
    with TestClient(create_app()) as c:
        monkeypatch.setenv("TASKBOARD_REVIEWER", "1")
        tid = _mk(c, "自动验收卡")
        c.post(f"/api/tasks/{tid}/move", json={"status": "待验收"})
        assert len(fired) == 1                    # 开: 到待验收即派验收
        c.post(f"/api/tasks/{tid}/move", json={"status": "执行中"})   # 打回重做
        c.post(f"/api/tasks/{tid}/move", json={"status": "待验收"})
        assert len(fired) == 2                    # 第二轮仍派
        c.post(f"/api/tasks/{tid}/move", json={"status": "执行中"})
        c.post(f"/api/tasks/{tid}/move", json={"status": "待验收"})
        assert len(fired) == 2                    # 上限 REVIEW_MAX=2: 转人工不再派
        tid2 = _mk(c, "人工卡", labels=["人工验收"])
        c.post(f"/api/tasks/{tid2}/move", json={"status": "待验收"})
        assert len(fired) == 2                    # 人工验收 label 拦住
        monkeypatch.delenv("TASKBOARD_REVIEWER")
        c.post(f"/api/tasks/{tid2}/move", json={"status": "执行中"})
        c.post(f"/api/tasks/{tid2}/move", json={"status": "待验收"})
        assert len(fired) == 2                    # 开关默认关: 不派
