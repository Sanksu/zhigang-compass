"""统一业务错误码与 HTTP 状态映射（设计文档 §2.4.7）。

错误码为响应体 code 字段；HTTP Status 为响应状态码。两者由本模块
集中维护，main.py 的全局异常处理器依赖此映射产出统一 APIResponse。
"""

from fastapi import HTTPException, status

from app.schemas.common import error_body

# 业务错误码（设计文档 §2.4.7）
ERR_VALIDATION = 4000
ERR_UNAUTHORIZED = 4010
ERR_TOKEN_EXPIRED = 4011
ERR_FORBIDDEN = 4030
ERR_NOT_FOUND = 4040
ERR_CONFLICT = 4090
ERR_RATE_LIMIT = 4290
ERR_INTERNAL = 5000
ERR_NEO4J = 5001
ERR_PGVECTOR = 5002
ERR_LLM_TIMEOUT = 5003

# 业务错误码 → HTTP 状态
ERROR_HTTP_STATUS = {
    ERR_VALIDATION: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ERR_UNAUTHORIZED: status.HTTP_401_UNAUTHORIZED,
    ERR_TOKEN_EXPIRED: status.HTTP_401_UNAUTHORIZED,
    ERR_FORBIDDEN: status.HTTP_403_FORBIDDEN,
    ERR_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ERR_CONFLICT: status.HTTP_409_CONFLICT,
    ERR_RATE_LIMIT: status.HTTP_429_TOO_MANY_REQUESTS,
    ERR_INTERNAL: status.HTTP_500_INTERNAL_SERVER_ERROR,
    ERR_NEO4J: status.HTTP_500_INTERNAL_SERVER_ERROR,
    ERR_PGVECTOR: status.HTTP_500_INTERNAL_SERVER_ERROR,
    ERR_LLM_TIMEOUT: status.HTTP_504_GATEWAY_TIMEOUT,
}

# HTTP 状态 → 业务错误码（HTTPException 全局处理器用；未列出的状态回落 5000）
HTTP_STATUS_ERROR_CODE = {
    status.HTTP_400_BAD_REQUEST: ERR_VALIDATION,
    status.HTTP_401_UNAUTHORIZED: ERR_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN: ERR_FORBIDDEN,
    status.HTTP_404_NOT_FOUND: ERR_NOT_FOUND,
    status.HTTP_409_CONFLICT: ERR_CONFLICT,
    status.HTTP_413_CONTENT_TOO_LARGE: ERR_VALIDATION,
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: ERR_VALIDATION,
    status.HTTP_429_TOO_MANY_REQUESTS: ERR_RATE_LIMIT,
    status.HTTP_500_INTERNAL_SERVER_ERROR: ERR_INTERNAL,
    status.HTTP_503_SERVICE_UNAVAILABLE: ERR_INTERNAL,
}


def business_error(code: int, msg: str, http_status: int | None = None) -> HTTPException:
    """构造带统一响应体的 HTTPException。

    http_status 缺省时按错误码映射（ERROR_HTTP_STATUS）；显式传入则优先。
    detail 为统一 APIResponse dict，由 main.py 的 HTTPException 全局处理器
    直接序列化，保证 {code, msg, data, trace_id} 格式一致。
    """
    status_code = http_status or ERROR_HTTP_STATUS.get(code, status.HTTP_200_OK)
    return HTTPException(
        status_code=status_code,
        detail=error_body(code, msg),
    )
