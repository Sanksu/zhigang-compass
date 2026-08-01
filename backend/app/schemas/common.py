"""统一响应模型。"""

from typing import Any, Generic, Optional, TypeVar
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


def error(code: int, msg: str) -> APIResponse:
    return APIResponse(code=code, msg=msg, data=None, trace_id=trace_id_var.get(""))
