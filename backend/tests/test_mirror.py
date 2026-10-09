"""镜像: 落点切换/旧解析器兼容/对账自愈/trash 留档 + 并发写一致性。"""
import threading

from app import mirror, service
from app.migrate_md import parse_card


def _files(home, sub=""):
    d = home / "tasks" / sub
    return sorted(p.name for p in d.glob("*.md")) if d.is_dir() else []


def test_mirror_placement_and_compat(home):
    d = service.create("镜像测试", body="## ① 任务\n\n干活", labels=["pdp"],
                       project="taskmirror", starred=True)
    t = d["task"]
    fp = home / "tasks" / f"{t['id']}.md"
    text = fp.read_text(encoding="utf-8")
    # 旧 board.py 解析器必须能读(过渡期回退保险)
    meta, body = parse_card(text)
    assert meta["id"] == t["id"] and meta["title"] == "镜像测试"
    assert meta["status"] == "待执行" and meta["owner"] == ""
    assert "project: taskmirror" in text and "starred: true" in text and '"pdp"' in text
    assert "自动生成" in text and "## ① 任务" in body

    service.add_log(t["id"], "第一行日志", actor="w1")
    assert "第一行日志" in fp.read_text(encoding="utf-8")

    service.move(t["id"], "完成")
    assert _files(home) == [] and _files(home, "done") == [f"{t['id']}.md"]
    service.restore(t["id"])
    assert _files(home) == [f"{t['id']}.md"] and _files(home, "done") == []

    service.delete(t["id"])
    assert _files(home) == []
    assert len(_files(home, "trash")) == 1  # 时间戳后缀留档


def test_full_sync_heals_tampering(home):
    t = service.create("对账卡")["task"]
    fp = home / "tasks" / f"{t['id']}.md"
    fp.write_text("人手乱改", encoding="utf-8")
    mirror.full_sync()
    meta, _ = parse_card(fp.read_text(encoding="utf-8"))
    assert meta["id"] == t["id"]
    # DB 里不存在的镜像文件被清走, trash 不动
    stray = home / "tasks" / "20200101-幽灵卡.md"
    stray.write_text("x", encoding="utf-8")
    mirror.full_sync()
    assert not stray.exists()


def test_concurrent_writes(home):
    errs = []

    def worker(i):
        try:
            for j in range(10):
                d = service.create(f"并发{i}-{j}")["task"]
                service.add_log(d["id"], f"log {j}")
                service.take(f"w{i}", d["id"])
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    b = service.board()
    assert len(b["tasks"]) == 80
    assert all(t["owner"].startswith("w") for t in b["tasks"])
    mirror.full_sync()
    assert len(_files(home)) == 80
    for p in (home / "tasks").glob("*.md"):
        meta, _ = parse_card(p.read_text(encoding="utf-8"))
        assert meta["status"] == "执行中"


def test_concurrent_take_single_card(home):
    t = service.create("独木桥")["task"]
    wins = []
    errs = []

    def worker(i):
        try:
            service.take(f"w{i}", t["id"])
            wins.append(i)
        except service.ApiError as e:
            if e.code not in (409,):
                errs.append(e)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [x.start() for x in ts]
    [x.join() for x in ts]
    assert not errs and len(wins) == 1
