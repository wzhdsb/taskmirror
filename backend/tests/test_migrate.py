"""旧卡导入: 解析/日志拆分/done 映射/幂等/坏行不丢。"""
from app import service
from app.migrate_md import main as migrate

LEGACY_ACTIVE = """---
id: 20261008-带日志旧卡
title: 带日志旧卡
status: 执行中
owner: "main"
created: 2026-10-08 10:00
updated: 2026-10-08 12:30
---

# 带日志旧卡

## ① 任务

做一件事

## 进展日志

- 10-08 10:00 [main] 建卡即认领
- 10-08 11:00 [main] 完成一半
不合规的行没有杠
"""

LEGACY_DONE = """---
id: 20261001-已归档旧卡
title: 已归档旧卡
status: 完成
owner: ""
created: 2026-10-01 09:00
updated: 2026-10-01 18:00
---

# 已归档旧卡

正文。

## 进展日志

- 10-01 18:00 [main] 交付
"""


def _legacy_dir(tmp_path):
    root = tmp_path / "legacy" / "tasks"  # main(src) 期望 src 就是旧 tasks 目录本身
    (root / "done").mkdir(parents=True, exist_ok=True)  # 幂等重跑会再调本函数
    (root / "20261008-带日志旧卡.md").write_text(LEGACY_ACTIVE, encoding="utf-8")
    (root / "done" / "20261001-已归档旧卡.md").write_text(LEGACY_DONE, encoding="utf-8")
    return root


def test_migrate(home, tmp_path, capsys):
    migrate(str(_legacy_dir(tmp_path)))
    d = service.detail("20261008-带日志旧卡")
    t = d["task"]
    assert t["status"] == "执行中" and t["owner"] == "main"
    assert "① 任务" in d["body"] and "进展日志" not in d["body"]
    assert "> 不合规的行没有杠" in d["body"]  # 坏行留正文不丢
    logs = [e for e in d["events"] if e["event"] == "log"]
    assert [e["detail"].get("text") for e in logs] == ["建卡即认领", "完成一半"]
    assert logs[0]["ts"] == "2026-10-08 10:00:00"  # 无年份取 created 年份
    assert (home / "tasks" / "20261008-带日志旧卡.md").exists()

    done = service.detail("20261001-已归档旧卡")["task"]
    assert done["status"] == "完成"
    assert (home / "tasks" / "done" / "20261001-已归档旧卡.md").exists()
    assert not (home / "tasks" / "20261001-已归档旧卡.md").exists()

    # 幂等: 重跑全部跳过
    migrate(str(_legacy_dir(tmp_path)))
    out = capsys.readouterr().out
    assert "导入 0 张" in out
    assert len(service.board()["tasks"]) == 1


def test_migrate_refresh(home, tmp_path, capsys):
    """--refresh: 已存在卡按旧目录覆盖重导(切换期带最新日志过来)。"""
    root = _legacy_dir(tmp_path)
    migrate(str(root))
    # 旧卡侧新增一条日志(模拟切换前主会话继续在旧系统写)
    fp = root / "20261008-带日志旧卡.md"
    fp.write_text(fp.read_text(encoding="utf-8") + "- 10-08 12:00 [main] 旧系统又写了一行\n", encoding="utf-8")
    migrate(str(root), refresh=True)
    assert "覆盖重导 2 张" in capsys.readouterr().out
    texts = [e["detail"].get("text") for e in service.detail("20261008-带日志旧卡")["events"] if e["event"] == "log"]
    assert texts[-1] == "旧系统又写了一行"
    assert len(service.board()["tasks"]) == 1  # 没有翻倍
