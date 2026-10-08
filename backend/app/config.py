"""集中配置。TASKBOARD_HOME 决定一切数据落点（DB / md 镜像 / 备份 / 日志）。"""
import os
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def home() -> Path:
    p = os.environ.get("TASKBOARD_HOME")
    return Path(p).resolve() if p else repo_root() / "data"


PORT = int(os.environ.get("TASKBOARD_PORT", "8801"))
HOST = os.environ.get("TASKBOARD_HOST", "127.0.0.1")
TOKEN = os.environ.get("TASKBOARD_TOKEN", "")  # 空 = 本地免鉴权
BACKUP_EVERY_N_WRITES = 50
BACKUP_KEEP = 20
