"""TaskMirror 开发任务脚本(纯 stdlib, Windows 比 make 可靠)。用法: python dev.py <cmd>

cmd: setup | dev | build | serve | test | e2e | migrate-legacy "旧tasks目录" [--dry-run] | mirror-sync | backup
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
NPM = "npm.cmd" if os.name == "nt" else "npm"  # Windows CreateProcess 不认无扩展名 npm


def run(*cmd, cwd=ROOT):
    print("+", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], cwd=str(cwd), check=True)


def env(**kw):
    return {**os.environ, "PYTHONUTF8": "1", **kw}


def setup():
    run(PY, "-m", "pip", "install", "-e", ".[dev]", cwd=BACKEND)
    run(NPM, "ci", cwd=FRONTEND)


def dev():
    """并行: uvicorn 8802 --reload + vite 5173(代理 /api)。"""
    procs = [
        subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--reload", "--port", "8802"],
                         cwd=str(BACKEND), env=env(TASKBOARD_PORT="8802")),
        subprocess.Popen([NPM, "run", "dev"], cwd=str(FRONTEND)),
    ]
    try:
        procs[0].wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()


def build():
    run(NPM, "run", "build", cwd=FRONTEND)


def serve():
    run(PY, "-m", "app.main", cwd=BACKEND)


def test():
    run(PY, "-m", "pytest", "tests", "-q", cwd=BACKEND)
    run(NPM, "run", "build", cwd=FRONTEND)  # tsc -b 即前端类型检查


def e2e():
    build()
    run(PY, str(ROOT / "e2e" / "e2e.py"))


def migrate_legacy():
    args = sys.argv[2:]
    if not args:
        sys.exit("用法: python dev.py migrate-legacy <旧tasks目录> [--dry-run]")
    run(PY, "-m", "app.migrate_md", *args, cwd=BACKEND)


def mirror_sync():
    run(PY, "-c", "from app import db, mirror; db.migrate(); mirror.full_sync(); print('ok')", cwd=BACKEND)


def backup():
    run(PY, "-c", "from app import db; db.migrate(); db.backup(); print('ok')", cwd=BACKEND)


CMDS = {
    "setup": setup, "dev": dev, "build": build, "serve": serve, "test": test,
    "e2e": e2e, "migrate-legacy": migrate_legacy, "mirror-sync": mirror_sync, "backup": backup,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        sys.exit(__doc__)
    CMDS[sys.argv[1]]()
