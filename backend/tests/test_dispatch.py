"""dispatch: 状态门槛/防重复/并发上限/stop打回/持久化恢复/activity解析。子进程全程 mock。"""
import json

from fastapi.testclient import TestClient

from app import service
from app.api import create_app


class FakeProc:
    def __init__(self):
        self.pid = 4242
        self.rc = None  # None=活着; 设值=已退出

    def poll(self):
        return self.rc

    def terminate(self):
        self.rc = 0

    def __enter__(self):  # nt 分支 subprocess.run 内部会 with Popen
        return self

    def __exit__(self, *a):
        return None


def setup(monkeypatch, home):
    service.DISPATCHES.clear()
    monkeypatch.setattr(service.shutil, "which", lambda _n: "fake-claude")
    procs: list[FakeProc] = []
    monkeypatch.setattr(
        service.subprocess, "Popen",
        lambda *a, **k: (procs.append(FakeProc()) or procs[-1]))
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: None)  # stop 的 taskkill 分支
    return procs


def test_dispatch_flow(home, monkeypatch):
    procs = setup(monkeypatch, home)
    with TestClient(create_app()) as c:
        tid = c.post("/api/tasks", json={"title": "测试派发"}).json()["task"]["id"]

        r = c.post(f"/api/tasks/{tid}/dispatch")
        assert r.status_code == 200 and r.json()["pid"] == 4242

        # 同卡重复派发 → 409
        assert c.post(f"/api/tasks/{tid}/dispatch").status_code == 409
        # 列表可见, agent 从最近事件派生(派发=executor / 验收=acceptor)
        row = c.get("/api/dispatches").json()["running"][0]
        assert row["task_id"] == tid and row["agent"] == "executor"

        # 假进程退出 → 任意请求触发 reap → 列表空, stop 404, 可再派
        procs[0].rc = 0
        assert c.get("/api/dispatches").json()["running"] == []
        assert c.post(f"/api/dispatches/{tid}/stop").status_code == 404
        assert c.post(f"/api/tasks/{tid}/dispatch").status_code == 200

        # 活进程 stop → 200, 卡打回待执行且 owner 清空
        assert c.post(f"/api/dispatches/{tid}/stop").status_code == 200
        assert c.get("/api/dispatches").json()["running"] == []
        t = c.get(f"/api/tasks/{tid}").json()["task"]
        assert t["status"] == "待执行" and t["owner"] == ""

        # 派发/退出/停止都有事件可追溯
        evs = [e["event"] for e in c.get(f"/api/tasks/{tid}").json()["events"]]
        assert "dispatched" in evs and "dispatch_stopped" in evs


def test_dispatch_guards(home, monkeypatch):
    procs = setup(monkeypatch, home)
    with TestClient(create_app()) as c:
        tid = c.post("/api/tasks", json={"title": "执行中卡"}).json()["task"]["id"]
        c.post(f"/api/tasks/{tid}/move", json={"status": "执行中", "before_id": None})
        # 非待执行 → 400
        assert c.post(f"/api/tasks/{tid}/dispatch").status_code == 400
        # 不存在的卡 → 404
        assert c.post("/api/tasks/nope/dispatch").status_code == 404

        # 并发上限: 4 张待执行卡各派一个, 第 5 张 → 429
        ids = [c.post("/api/tasks", json={"title": f"并发卡{i}"}).json()["task"]["id"] for i in range(5)]
        for i in ids[:4]:
            assert c.post(f"/api/tasks/{i}/dispatch").status_code == 200
        assert c.post(f"/api/tasks/{ids[4]}/dispatch").status_code == 429
        assert len(procs) == 4  # 400/404 路径不应触达 Popen


def test_dispatch_resume(home, monkeypatch):
    """服务重启: 表里 pid 活 → 收养; pid 死 → 清账+失联事件。"""
    setup(monkeypatch, home)
    with TestClient(create_app()) as c:
        tid = c.post("/api/tasks", json={"title": "恢复测试"}).json()["task"]["id"]
        c.post(f"/api/tasks/{tid}/dispatch")
        assert tid in service.DISPATCHES

    # 模拟重启: 进程表清空, pid 4242 不存在 → 恢复时应清孤儿
    service.DISPATCHES.clear()
    with TestClient(create_app()) as c:
        assert tid not in service.DISPATCHES  # 死 pid 被清
        evs = [e["event"] for e in c.get(f"/api/tasks/{tid}").json()["events"]]
        assert "dispatch_lost" in evs

    # 活 pid 场景: 手动造表再重启 → 收养 (log 用绝对路径, 免得 cwd 被污染出垃圾文件)
    from app import db
    log = home / "logs" / "resume.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.touch()
    with db.conn() as conn:
        conn.execute("INSERT OR REPLACE INTO dispatches VALUES(?,?,?,?)",
                     (tid, 99999, str(log), "2026-10-09 00:00:00"))
    monkeypatch.setattr(service, "_pid_alive", lambda pid: True)
    with TestClient(create_app()) as c:
        assert tid in service.DISPATCHES
        assert service.DISPATCHES[tid]["pid"] == 99999


def test_activity_parse(home):
    """stream-json 行 → 人读活动项: tool_use 缩名取参, text 截断, result 收尾。"""
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "timestamp": "2026-10-08T20:31:58.899Z", "message": {"content": [
            {"type": "tool_use", "name": "mcp__taskmirror__take_task",
             "input": {"id": "20261009-x", "owner": "executor"}}]}}),
        json.dumps({"type": "assistant", "timestamp": "2026-10-08T20:32:30.000Z", "message": {"content": [
            {"type": "text", "text": "已认领, 开始读取资料 " * 20}]}}),
        json.dumps({"type": "result", "result": "任务完成, 已置待验收"}),
    ]
    acts = service._parse_activity(lines)
    from datetime import datetime
    want_ts = datetime.fromisoformat("2026-10-08T20:31:58.899+00:00").astimezone().strftime("%H:%M:%S")
    assert acts[0] == {"kind": "tool", "text": "take_task(20261009-x)", "ts": want_ts}  # UTC→本机时区, 不硬编码时区
    assert acts[1]["kind"] == "text" and len(acts[1]["text"]) == 110
    assert acts[-1]["kind"] == "done" and "待验收" in acts[-1]["text"]
    # system/hook 行被过滤
    assert len(acts) == 3
