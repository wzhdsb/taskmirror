"""SQLite 接入: WAL + busy_timeout, 线程局部连接(路径变更时自动重连), user_version 手写迁移,
在线备份, 全局 rev 计数(Web 与 MCP 双进程感知写操作的唯一点)。"""
import sqlite3
import threading
import time
from pathlib import Path

from . import config

_local = threading.local()

MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE boards (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, created TEXT NOT NULL)""",
        """CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            board_id INTEGER NOT NULL DEFAULT 1 REFERENCES boards(id),
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '待执行'
                CHECK(status IN('待执行','执行中','待验收','完成')),
            owner TEXT NOT NULL DEFAULT '',
            priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN('low','normal','high','urgent')),
            labels TEXT NOT NULL DEFAULT '[]',
            color TEXT,
            due TEXT,
            body TEXT NOT NULL DEFAULT '',
            position INTEGER NOT NULL DEFAULT 0,
            created TEXT NOT NULL, updated TEXT NOT NULL)""",
        "CREATE INDEX idx_tasks_board ON tasks(board_id, status, position)",
        "CREATE INDEX idx_tasks_owner ON tasks(owner)",
        """CREATE TABLE task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL, ts TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT 'webui',
            event TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}')""",
        "CREATE INDEX idx_events_task ON task_events(task_id, id)",
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "INSERT INTO boards(id, name, created) VALUES (1, '主看板', datetime('now','localtime'))",
    ],
    2: [
        # 网页派发的 AI 进程注册表: 服务重启后据此恢复/清理孤儿
        """CREATE TABLE dispatches (
            task_id TEXT PRIMARY KEY, pid INTEGER NOT NULL,
            log TEXT NOT NULL, started TEXT NOT NULL)""",
    ],
}

_backup_lock = threading.Lock()
_write_count = 0


def db_path() -> Path:
    return config.home() / "board.db"


def conn() -> sqlite3.Connection:
    want = str(db_path())
    c = getattr(_local, "conn", None)
    if c is not None and getattr(_local, "path", None) == want:
        return c
    if c is not None:
        c.close()
    config.home().mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(want, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("PRAGMA foreign_keys=ON")
    _local.conn, _local.path = c, want
    return c


def reset():  # 测试用: 清线程连接缓存
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
        _local.conn = _local.path = None


def migrate():
    c = conn()
    v = c.execute("PRAGMA user_version").fetchone()[0]
    for version in sorted(MIGRATIONS):
        if version > v:
            with c:
                for stmt in MIGRATIONS[version]:
                    c.execute(stmt)
                c.execute(f"PRAGMA user_version={version}")


# ---------- rev: 全局写计数, SSE 据此通知前端重拉 ----------

def bump_rev(c: sqlite3.Connection) -> int:
    c.execute(
        "INSERT INTO meta(key, value) VALUES('rev','1') "
        "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1")
    return int(c.execute("SELECT value FROM meta WHERE key='rev'").fetchone()[0])


def get_rev() -> int:
    row = conn().execute("SELECT value FROM meta WHERE key='rev'").fetchone()
    return int(row[0]) if row else 0


# ---------- 备份 ----------

def backup():
    with _backup_lock:
        dest_dir = config.home() / "backups"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"board-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time())}.db"
        dst = sqlite3.connect(dest)
        try:
            conn().backup(dst)
        finally:
            dst.close()
        olds = sorted(dest_dir.glob("board-*.db"))
        for p in olds[:-config.BACKUP_KEEP]:
            p.unlink(missing_ok=True)


def maybe_backup():
    """service 每次写操作后调用; 累积 N 次写做一次在线备份。"""
    global _write_count
    _write_count += 1
    if _write_count >= config.BACKUP_EVERY_N_WRITES:
        _write_count = 0
        backup()
