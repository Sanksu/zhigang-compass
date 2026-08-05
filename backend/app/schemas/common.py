"""统一响应模型。"""

from typing import Any, Generic, Optional, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.middleware import trace_id_var

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    code: int = 0
    msg: str = "ok"
    data: Optional[T] = None
    trace_id: str = ""


def ok(data: Any = None, msg: str = "ok") -> APIResponse:
    return APIResponse(code=0, msg=msg, data=data, trace_id=trace_id_var.get(""))


def error(code: int, msg: str, http_status: int = 200):
    """构造统一错误响应体（{code, msg, data, trace_id}）。

    http_status 默认 200 保持旧行为（兼容现有调用）；契约明确的错误
    （设计文档 §2.4.7，如 4010/4030/4040）显式传入对应 HTTP 状态码，
    返回带该状态码的 JSONResponse。
    """
    body = APIResponse(code=code, msg=msg, data=None, trace_id=trace_id_var.get(""))
    if http_status == 200:
        return body
    return JSONResponse(status_code=http_status, content=body.model_dump())
