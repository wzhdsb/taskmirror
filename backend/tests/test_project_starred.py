"""泳道分组(project) + 星标(starred): CRUD/镜像 frontmatter + v5 迁移回填语句。"""
import pytest
from fastapi.testclient import TestClient

from app import db, service
from app.api import create_app


@pytest.fixture()
def client(home):
    with TestClient(create_app()) as c:
        yield c


def _mk(client, title, **kw):
    r = client.post("/api/tasks", json={"title": title, **kw})
    assert r.status_code == 201, r.text
    return r.json()["task"]


def test_project_starred_crud(client, home):
    t = _mk(client, "泳道测试卡", project="taskmirror", starred=True)
    cid = t["id"]
    assert t["project"] == "taskmirror" and t["starred"] == 1

    r = client.patch(f"/api/tasks/{cid}", json={"project": "plan", "starred": False})
    assert r.json()["task"]["project"] == "plan" and r.json()["task"]["starred"] == 0
    fp = home / "tasks" / f"{cid}.md"
    fm = fp.read_text(encoding="utf-8").split("---")[1]
    assert "project: plan" in fm and "starred" not in fm

    client.patch(f"/api/tasks/{cid}", json={"starred": True})
    assert "starred: true" in fp.read_text(encoding="utf-8").split("---")[1]
    evs = client.get(f"/api/tasks/{cid}").json()["events"]
    assert any(e["event"] == "project" and e["detail"]["to"] == "plan" for e in evs)
    assert any(e["event"] == "starred" and e["detail"]["to"] is True for e in evs)


def test_migration_backfill(home):
    """v5 数据语句回放: urgent→星标, priority 归 normal, 标题前缀回填 project。"""
    service.create("boboai-web 发版收尾", priority="urgent")
    service.create("legal-advisor 某卡")
    service.create("BandaiHunterPC 某卡")
    service.create("无关标题卡")
    c = db.conn()
    with c:
        c.execute("UPDATE tasks SET project='', starred=0")  # 模拟迁移前状态
        for stmt in db.MIGRATIONS[5]:
            if not stmt.startswith("ALTER"):  # 列建库即有, 只回放数据语句
                c.execute(stmt)
    rows = {r["title"]: r for r in
            c.execute("SELECT title, project, starred, priority FROM tasks")}
    assert rows["boboai-web 发版收尾"]["project"] == "boboai-web"
    assert rows["boboai-web 发版收尾"]["starred"] == 1  # urgent 自动转星标
    assert rows["legal-advisor 某卡"]["project"] == "legal-advisor"
    assert rows["BandaiHunterPC 某卡"]["project"] == "BandaiHunterPC"
    assert rows["无关标题卡"]["project"] == ""  # 未分组
    assert all(r["priority"] == "normal" for r in rows.values())
