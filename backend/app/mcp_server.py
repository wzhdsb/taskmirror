"""MCP stdio server (FastMCP): 复用 service 直写 SQLite。
双进程架构关键: Web 服务挂了, MCP 建卡/捞卡照常 —— 保住旧系统最珍贵性质。"""
from . import service
from .service import ApiError

try:
    from mcp.server.fastmcp import FastMCP  # mcp 1.x
except ImportError:
    try:
        from mcp.server.mcpserver import MCPServer as FastMCP  # mcp 2.x: FastMCP 改名
    except ImportError:  # 允许后端测试环境无 mcp 包
        FastMCP = None

STATUS_NOTE = "待执行/执行中/待验收/完成"


def _md_detail(d: dict) -> str:
    t = d["task"]
    lines = [
        f"# {t['title']}",
        f"id: {t['id']} | status: {t['status']} | owner: {t['owner'] or '-'} | "
        f"priority: {t['priority']} | labels: {'、'.join(t['labels']) or '-'} | "
        f"due: {t['due'] or '-'} | created: {t['created']} | updated: {t['updated']}",
        "",
        d["body"] or "(无正文)",
        "",
        "## 进展日志",
    ]
    for e in d["events"]:
        lines.append(f"- {e['ts'][5:16]} [{e['actor']}] "
                     f"{e['detail'].get('text') or e['event']}")
    return "\n".join(lines)


if FastMCP is not None:
    mcp = FastMCP("taskmirror")

    @mcp.tool()
    def list_tasks(status: str = "", owner: str = "", limit: int = 50) -> str:
        """列出任务卡。status: 待执行/执行中/待验收/完成, 留空=非完成全部; owner 留空=全部。
        返回紧凑清单: 每行 `id | status | owner | title`。"""
        try:
            b = service.board()
            rows = b["tasks"] + b["done"]
            if status:
                rows = [t for t in rows if t["status"] == status]
            if owner:
                rows = [t for t in rows if t["owner"] == owner]
            rows = rows[: max(1, min(limit, 200))]
            if not rows:
                return "没有匹配的任务卡。"
            return "\n".join(
                f"{t['id']} | {t['status']} | {t['owner'] or '-'} | {t['title']}"
                + (f" | ⏳前置未完成: {','.join(t['waiting'])}" if t.get("waiting") else "")
                + (f" | ⧉{len(t['related']) + len(t['relatesBack'])}" if t.get("related") or t.get("relatesBack") else "")
                for t in rows)
        except ApiError as e:
            return f"错误: {e.msg}"

    @mcp.tool()
    def get_task(id: str) -> str:
        """按 id 取任务卡全貌: 四段式正文 + 完整进展日志。"""
        try:
            return _md_detail(service.detail(id))
        except ApiError as e:
            return f"错误: {e.msg}"

    @mcp.tool()
    def create_task(title: str, body: str = "", labels: str = "", priority: str = "normal",
                    due: str = "") -> str:
        """建卡。labels 逗号分隔; priority: low/normal/high/urgent; due: YYYY-MM-DD。返回新卡 id。"""
        try:
            d = service.create(title, body, labels.split(",") if labels else [],
                               priority, due or None)
            return f"已建卡: {d['task']['id']} (待执行)"
        except ApiError as e:
            return f"错误: {e.msg}"

    @mcp.tool()
    def take_task(owner: str, id: str = "") -> str:
        """认领任务卡: 原子防抢, 待执行→执行中。id 留空=自动捞最旧的无人待执行卡
        (被未完成 depends 前置阻塞的卡自动跳过; 有 id 但前置未完成会报错, 先推进前置或换卡)。"""
        try:
            d = service.take(owner, id or None)
            t = d["task"]
            return f"已认领 {t['id']} 「{t['title']}」→ 执行中。记得完成后 set_status 到 待验收。"
        except ApiError as e:
            return f"错误: {e.msg}"

    @mcp.tool()
    def relate_task(id: str, target: str, kind: str = "related", remove: bool = False) -> str:
        """维护卡间关联(单向声明, 反查自动算): related=同源配套, depends=我的前置(先做完它才能做我)。
        target 可为归档卡; 归档卡不能发起。remove=true 摘除。幂等。"""
        try:
            d = service.relate(id, target, kind, remove)
            return f"已{'摘除' if remove else '关联'} {d['task']['id']} {kind} {target}"
        except ApiError as e:
            return f"错误: {e.msg}"

    @mcp.tool()
    def update_task(id: str, title: str = "", body: str = "", owner: str = "",
                    labels: str = "", priority: str = "", due: str = "") -> str:
        """更新任务卡字段。留空=不改(labels 逗号分隔)。注意: 无法用本工具清空字段, 清空走 web。"""
        try:
            kw = {}
            if title:
                kw["title"] = title
            if body:
                kw["body"] = body
            if owner:
                kw["owner"] = owner
            if labels:
                kw["labels"] = labels.split(",")
            if priority:
                kw["priority"] = priority
            if due:
                kw["due"] = due
            if not kw:
                return "没有要更新的字段。"
            service.patch(id, **kw)
            return f"已更新 {id}: {', '.join(kw)}"
        except ApiError as e:
            return f"错误: {e.msg}"

    @mcp.tool()
    def set_status(id: str, status: str) -> str:
        """改状态(列): 待执行/执行中/待验收/完成。完成=归档(入 done/ 镜像)。"""
        try:
            service.move(id, status)
            return f"{id} → {status}"
        except ApiError as e:
            return f"错误: {e.msg}"

    @mcp.tool()
    def add_log(id: str, text: str) -> str:
        """给任务卡追加一行进展日志(也用作执行心跳)。单行, 自动截 300 字。"""
        try:
            d = service.add_log(id, text)
            return f"已记录到 {d['task']['id']}"
        except ApiError as e:
            return f"错误: {e.msg}"

    @mcp.tool()
    def get_insights() -> str:
        """看板体检(adviser 规则层): 负载/停滞卡/验收滞留/due 临近/空列建议。
        主会话当 adviser 时先读这个, 再决定拆卡/催办/验收。"""
        i = service.insights()
        lines = [f"## 体检 {i['generated']}  (待执行 {i['counts'].get('待执行', 0)} / "
                 f"执行中 {i['counts'].get('执行中', 0)} / 待验收 {i['counts'].get('待验收', 0)})"]
        if i["load"]:
            lines.append("负载: " + "; ".join(
                f"{d['owner']} 在手{d['active']} 待验收{d['review']}" for d in i["load"]))
        for key, head in (("stalled", "停滞"), ("review_overdue", "验收滞留"),
                          ("overdue", "已逾期"), ("due_soon", "临期")):
            for t in i[key]:
                extra = t.get("hours", f"{t.get('due')}")
                lines.append(f"- [{head}] {t['id']} 「{t['title']}」 {t.get('owner') or t.get('status', '')} {extra}")
        if not i["suggestions"]:
            lines.append("无建议: 看板健康。")
        else:
            lines.extend(f"- 建议: {s}" for s in i["suggestions"])
        return "\n".join(lines)
