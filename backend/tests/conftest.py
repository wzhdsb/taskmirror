"""TASKBOARD_HOME 指向 tmp, 每测清线程连接缓存 → 完全隔离。"""
import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKBOARD_HOME", str(tmp_path))
    from app import db
    db.reset()
    db.migrate()  # 裸 service 测试也需要表
    yield tmp_path
    db.reset()
