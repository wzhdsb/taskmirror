"""REST + SSE + 静态前端托管 + 单 token 鉴权。路由薄, 逻辑全在 service。"""
import asyncio
import json
import logging
import time
from pathlib import Path

import tomllib
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, mirror, service
from .models import LogReq, MoveReq, RelateReq, TaskCreate, TaskPatch
from .service import ApiError

# 版本号唯一真源 = backend/pyproject.toml(editable 安装的元数据会过期, 不读它)
_VERSION = tomllib.loads(Path(__file__).resolve().parent.parent.joinpath("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

SSE_POLL = 1.0


class _JsonFormatter(logging.Formatter):
    def format(self, r: logging.LogRecord) -> str:
        return json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "level": r.levelname,
                           "msg": r.getMessage()}, ensure_ascii=False)


def setup_log() -> logging.Logger:
    """JSON 行日志落 data/logs/app-YYYYMMDD.jsonl, 幂等。"""
    log = logging.getLogger("taskmirror")
    if not log.handlers:
        d = config.home() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        h = logging.FileHandler(d / f"app-{time.strftime('%Y%m%d')}.jsonl", encoding="utf-8")
        h.setFormatter(_JsonFormatter())
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log


async def _rev_stream(request):
    """rev 变化才发一行 `data: {rev}`; 断连即停。模块级以便有界测试。"""
    last = -1
    while True:
        if await request.is_disconnected():
            return
        rev = db.get_rev()
        if rev != last:
            last = rev
            yield f"data: {rev}\n\n"
        await asyncio.sleep(SSE_POLL)


def create_app() -> FastAPI:
    db.migrate()
    mirror.full_sync()
    service.resume_dispatches()  # 服务重启: 收养活 AI 进程 / 清孤儿
    log = setup_log()
    app = FastAPI(title="TaskMirror", docs_url=None, redoc_url=None)

    @app.exception_handler(ApiError)
    async def _api_err(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.code, content={"error": exc.msg})

    @app.middleware("http")
    async def _auth_access(request: Request, call_next):
        if config.TOKEN and request.url.path.startswith(("/api",)):
            auth = request.headers.get("Authorization", "")
            ok = auth == f"Bearer {config.TOKEN}" or request.query_params.get("token") == config.TOKEN
            if not ok:
                return JSONResponse(status_code=401, content={"error": "未授权"})
        t0 = time.time()
        resp = await call_next(request)
        if request.url.path.startswith("/api"):
            log.info("%s %s %s %dms", request.method, request.url.path, resp.status_code,
                     round((time.time() - t0) * 1000))
        return resp

    # ---------- REST ----------

    @app.get("/api/board")
    async def board():
        return service.board()

    @app.post("/api/tasks", status_code=201)
    async def create_task(req: TaskCreate):
        return service.create(**req.model_dump())

    @app.get("/api/tasks/{cid}")
    async def get_task(cid: str):
        return service.detail(cid)

    @app.patch("/api/tasks/{cid}")
    async def patch_task(cid: str, req: TaskPatch):
        return service.patch(cid, actor="webui", **req.model_dump(exclude_unset=True))

    @app.post("/api/tasks/{cid}/move")
    async def move_task(cid: str, req: MoveReq):
        return service.move(cid, req.status, req.before_id, actor="webui")

    @app.post("/api/tasks/{cid}/log")
    async def log_task(cid: str, req: LogReq):
        return service.add_log(cid, req.text, req.actor)

    @app.post("/api/tasks/{cid}/relate")
    async def relate_task(cid: str, req: RelateReq):
        return service.relate(cid, req.target, req.kind, req.remove)

    @app.post("/api/tasks/{cid}/restore")
    async def restore_task(cid: str):
        return service.restore(cid)

    @app.delete("/api/tasks/{cid}")
    async def delete_task(cid: str):
        return service.delete(cid)

    @app.get("/api/events/recent")
    async def events_recent(limit: int = 100):
        return {"events": service.recent_events(limit)}

    @app.get("/api/insights")
    async def insights():
        return service.insights()

    # ---------- 派发: 网页一键起 AI 执行者(claude -p + task-executor agent), 逻辑在 service ----------

    @app.post("/api/tasks/{cid}/dispatch")
    def dispatch_task(cid: str):
        return service.dispatch(cid)

    @app.post("/api/tasks/{cid}/review")
    def review_task(cid: str):
        return service.review(cid)

    @app.get("/api/dispatches")
    async def list_dispatches():
        return {"running": service.running()}

    @app.get("/api/dispatches/{cid}/activity")
    async def dispatch_activity(cid: str):
        return service.activity(cid)

    @app.post("/api/dispatches/{cid}/stop")
    def stop_dispatch(cid: str):
        return service.stop_dispatch(cid)

    # ---------- SSE: rev 广播, 客户端收到变化后重拉 /api/board ----------

    @app.get("/api/events")
    async def sse(request: Request):
        return StreamingResponse(_rev_stream(request), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/meta")
    async def meta():
        return {"rev": db.get_rev(), "home": str(config.home()), "auth": bool(config.TOKEN),
                "version": _VERSION}

    # ---------- 静态前端与兼容路由 ----------

    @app.get("/board")
    async def board_redirect():
        return RedirectResponse("/", status_code=301)

    dist = config.repo_root() / "frontend" / "dist"
    if (dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/")
        async def index():
            return FileResponse(dist / "index.html")

    return app


def run():
    import uvicorn
    uvicorn.run(create_app(), host=config.HOST, port=config.PORT, log_level="warning")
