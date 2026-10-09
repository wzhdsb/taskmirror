"""关联依赖: relate 维护/反查现算/waiting 剔除/待执行沉底/take 跳过与409/归档守卫/重开/需求缺省稿。"""

from fastapi.testclient import TestClient

from app.api import create_app


def _mk(c, title, **kw):
    return c.post("/api/tasks", json={"title": title, **kw}).json()["task"]["id"]


def test_relate_flow(home):
    with TestClient(create_app()) as c:
        a, b, d = _mk(c, "关联A"), _mk(c, "关联B"), _mk(c, "关联D")
        # 添加: depends=我的前置
        assert c.post(f"/api/tasks/{a}/relate", json={"target": d, "kind": "depends"}).status_code == 200
        assert c.post(f"/api/tasks/{a}/relate", json={"target": b}).status_code == 200  # 缺省 related
        # 重复幂等(不报错不加)
        c.post(f"/api/tasks/{a}/relate", json={"target": b})
        t = c.get(f"/api/tasks/{a}").json()["task"]
        assert t["related"] == [b] and t["depends"] == [d]

        # 守卫: 自己/不存在/非法 kind
        assert c.post(f"/api/tasks/{a}/relate", json={"target": a}).status_code == 400
        assert c.post(f"/api/tasks/{a}/relate", json={"target": "nope"}).status_code == 404
        assert c.post(f"/api/tasks/{a}/relate", json={"target": b, "kind": "bad"}).status_code == 400

        # 反查现算 + waiting: d 未完成 → a.waiting=[d]; d.dependedBy=[a]
        board = c.get("/api/board").json()
        by = {t["id"]: t for t in board["tasks"] + board["done"]}
        assert by[a]["waiting"] == [d]
        assert by[d]["dependedBy"] == [a] and by[a]["relatesBack"] == []

        # d 完成 → waiting 消化, d 仍在 done 里可被反查
        c.post(f"/api/tasks/{d}/move", json={"status": "完成", "before_id": None})
        board = c.get("/api/board").json()
        by = {t["id"]: t for t in board["tasks"] + board["done"]}
        assert by[a]["waiting"] == [] and by[d]["status"] == "完成"

        # 指向不存在卡的 depends 不阻塞(视已消化)
        c.post(f"/api/tasks/{b}/relate", json={"target": "ghost-id", "kind": "depends"})
        assert c.get("/api/board").json()["tasks"][0]["id"] and \
            {t["id"]: t for t in c.get("/api/board").json()["tasks"]}[b]["waiting"] == []

        # 摘除
        c.post(f"/api/tasks/{a}/relate", json={"target": b, "remove": True})
        assert c.get(f"/api/tasks/{a}").json()["task"]["related"] == []
        # 事件可追溯
        evs = [e["event"] for e in c.get(f"/api/tasks/{a}").json()["events"]]
        assert evs.count("relate") >= 3


def test_take_blocked(home):
    from app import service
    with TestClient(create_app()) as c:
        pre, blocked, free = _mk(c, "前置"), _mk(c, "被阻塞"), _mk(c, "自由卡")
        c.post(f"/api/tasks/{blocked}/relate", json={"target": pre, "kind": "depends"})
        # 前置自身也在干(移出待执行池), 避免它先被捞走
        c.post(f"/api/tasks/{pre}/move", json={"status": "执行中", "before_id": None})
        # 无参捞卡: 跳过被阻塞的 blocked, 捞到 free (blocked 更早)
        assert service.take("executor")["task"]["id"] == free
        # 有参: 前置未完成 → 409 带清单
        try:
            service.take("executor", blocked)
            assert False, "应 409"
        except service.ApiError as e:
            assert e.code == 409 and pre in e.msg
        # 剩余待执行无人卡全被阻塞 → 404
        b2 = _mk(c, "被阻塞2")
        c.post(f"/api/tasks/{b2}/relate", json={"target": pre, "kind": "depends"})
        try:
            service.take("executor2")
            assert False, "应 404"
        except service.ApiError as e:
            assert e.code == 404
        # insights 报被阻塞卡
        ins = c.get("/api/insights").json()
        assert any("被未完成前置阻塞" in s and blocked in s for s in ins["suggestions"])


def test_board_sink_and_reopen(home):
    with TestClient(create_app()) as c:
        pre = _mk(c, "前置X")
        free, blk = _mk(c, "自由Y"), _mk(c, "被阻塞Z")
        c.post(f"/api/tasks/{blk}/relate", json={"target": pre, "kind": "depends"})
        # 待执行列: waiting=0 的在前
        col = [t["id"] for t in c.get("/api/board").json()["tasks"] if t["status"] == "待执行"]
        assert col.index(free) < col.index(blk)

        # 归档守卫: 完成→拒 patch body/owner; 仅可重开(移回前四态)
        done_id = _mk(c, "将归档")
        c.post(f"/api/tasks/{done_id}/move", json={"status": "完成", "before_id": None})
        assert c.patch(f"/api/tasks/{done_id}", json={"body": "x"}).status_code == 400
        assert c.post(f"/api/tasks/{done_id}/relate", json={"target": pre}).status_code == 400
        # 归档卡可作 target
        assert c.post(f"/api/tasks/{pre}/relate", json={"target": done_id}).status_code == 200
        # 重开: 完成→执行中, 回 active 列
        assert c.post(f"/api/tasks/{done_id}/move", json={"status": "执行中", "before_id": None}).status_code == 200
        assert c.get(f"/api/tasks/{done_id}").json()["task"]["status"] == "执行中"


def test_create_default_body(home):
    with TestClient(create_app()) as c:
        tid = _mk(c, "快速卡")
        assert c.get(f"/api/tasks/{tid}").json()["task"]["body"] == "## 需求"
        tid2 = _mk(c, "四段卡", body="## ① 任务\nxx")
        assert c.get(f"/api/tasks/{tid2}").json()["task"]["body"].startswith("## ①")


def test_depends_cycle_guard(home):
    """环防护: 直接环与三连环均 400, related 不受限, 摘除后可重建。"""
    with TestClient(create_app()) as c:
        a, b, d = _mk(c, "环A"), _mk(c, "环B"), _mk(c, "环D")
        assert c.post(f"/api/tasks/{a}/relate", json={"target": b, "kind": "depends"}).status_code == 200
        # 两连环: B→A
        assert c.post(f"/api/tasks/{b}/relate", json={"target": a, "kind": "depends"}).status_code == 400
        # 三连环: A→B→D 后 D→A 成环
        assert c.post(f"/api/tasks/{b}/relate", json={"target": d, "kind": "depends"}).status_code == 200
        assert c.post(f"/api/tasks/{d}/relate", json={"target": a, "kind": "depends"}).status_code == 400
        # related 同目标不受限
        assert c.post(f"/api/tasks/{d}/relate", json={"target": a}).status_code == 200
        # 摘掉环上 B→D 后 D→A 可建
        c.post(f"/api/tasks/{b}/relate", json={"target": d, "kind": "depends", "remove": True})
        assert c.post(f"/api/tasks/{d}/relate", json={"target": a, "kind": "depends"}).status_code == 200


def test_mirror_related_keys(home):
    """镜像 frontmatter 非空才写 related/depends, 逗号分隔。"""
    with TestClient(create_app()) as c:
        a, b = _mk(c, "镜像A"), _mk(c, "镜像B")
        c.post(f"/api/tasks/{a}/relate", json={"target": b, "kind": "depends"})
        text_a = (home / "tasks" / f"{a}.md").read_text(encoding="utf-8")
        assert f"depends: {b}" in text_a and "related:" not in text_a
        text_b = (home / "tasks" / f"{b}.md").read_text(encoding="utf-8")
        assert "related:" not in text_b and "depends:" not in text_b
