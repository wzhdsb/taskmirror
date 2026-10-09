"""请求体校验模型(pydantic)。响应一律 dict, 由 service 统一序列化。"""

from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = ""
    labels: list[str] = []
    priority: str = "normal"  # 已退役: 兼容旧调用方, 后端不再区分
    due: str | None = None  # YYYY-MM-DD
    color: str | None = None
    project: str = ""  # 泳道分组
    starred: bool = False  # 星标=下一批要做的


class TaskPatch(BaseModel):
    title: str | None = None
    owner: str | None = None
    body: str | None = None
    labels: list[str] | None = None
    priority: str | None = None  # 已退役, 仍接受旧参数
    due: str | None = None
    color: str | None = None
    project: str | None = None
    starred: bool | None = None


class MoveReq(BaseModel):
    status: str
    before_id: str | None = None  # 插到该卡之前; None=追加列尾


class LogReq(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    actor: str = "webui"


class RelateReq(BaseModel):
    target: str = Field(min_length=1, max_length=100)  # 目标卡 id, 可为归档卡
    kind: str = "related"  # related=同源配套 | depends=我的前置
    remove: bool = False
