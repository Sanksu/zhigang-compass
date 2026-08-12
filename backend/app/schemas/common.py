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


def error_body(code: int, msg: str) -> dict:
    """统一错误响应体 dict（{code, msg, data, trace_id}）。

    error()（JSONResponse）与 business_error()（HTTPException detail）
    共用，保证两路径响应体格式一致。
    """
    return APIResponse(code=code, msg=msg, data=None, trace_id=trace_id_var.get("")).model_dump()


def error(code: int, msg: str, http_status: int | None = None):
    """构造统一错误响应体（{code, msg, data, trace_id}）。

    http_status 未显式传入时按 code 推导：code 为业务错误码（4000/4010/...）
    查 ERROR_HTTP_STATUS 映射；code 本身是标准 HTTP 状态码（400-599）则直接用；
    其余保持 200（非契约错误码的兼容行为）。返回带对应状态码的 JSONResponse。
    """
    if http_status is None:
        http_status = _infer_http_status(code)
    return JSONResponse(status_code=http_status, content=error_body(code, msg))


def _infer_http_status(code: int) -> int:
    """由错误码推导 HTTP 状态码（设计文档 §2.4.7 映射）。"""
    # 业务错误码映射表在 errors.py，延迟导入避免循环依赖
    from app.core.errors import ERROR_HTTP_STATUS

    if code in ERROR_HTTP_STATUS:
        return ERROR_HTTP_STATUS[code]
    if 400 <= code < 600:
        return code
    return 200
