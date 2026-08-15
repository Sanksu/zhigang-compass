"""FastAPI 入口。

启动：uv run uvicorn app.main:app --reload --port 8000
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from neo4j.exceptions import GqlError

from app.core.config import settings
from app.core.errors import (
    ERR_INTERNAL,
    ERR_NEO4J,
    ERR_PGVECTOR,
    ERR_VALIDATION,
    ERROR_HTTP_STATUS,
    HTTP_STATUS_ERROR_CODE,
)
from app.core.middleware import setup_middleware, trace_id_var
from app.schemas.common import APIResponse
from app.services.embeddings.vector_store import PgvectorUnavailableError
from app.api.v1 import router as v1_router

# 应用内 logger（auth/admin 等模块）输出到标准输出，便于运行排错；
# root 默认 WARNING 会吞掉模块 INFO 日志，故启动时统一配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时
    if settings.is_production:
        if settings.secret_key == "change-me-in-production":
            raise RuntimeError("SECRET_KEY 未修改，生产环境拒绝启动")
        if settings.admin_password == "admin123":
            raise RuntimeError("ADMIN_PASSWORD 仍为默认弱口令，生产环境拒绝启动")
    await _prewarm_semantic()
    yield
    # 关闭时 — 资源由各自模块管理


async def _prewarm_semantic() -> None:
    """启动时同步预加载 SBERT 模型（08-15 修复：比对详情白屏）。

    此前后台线程预加载——api 冷启动后首次 compare 实测 16.3s（模型加载 +
    编码），用户感知"加载比对详情…白屏一段时间"。改为启动阶段同步等待
    （实测 16s 在 healthcheck start_period 窗口内），用户请求永不感知加载。
    失败静默（语义不可用时匹配自动降级纯规则，见 semantic.SemanticUnavailableError）。
    """
    import asyncio

    from app.services.matching.semantic import SkillEmbedder

    try:
        await asyncio.to_thread(SkillEmbedder.get().preload)
        # 预热岗位技能向量（08-15 修复：首次 compare 的 positions 加载 +
        # 批量 encode 实测 16s——比模型加载更长，用户"比对详情白屏"主因）
        from app.services.matching.loaders import load_positions_from_graph

        await asyncio.to_thread(load_positions_from_graph)
    except Exception:
        pass


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url=None,
)

setup_middleware(app)

app.include_router(v1_router, prefix="/api/v1")


# ---------- 全局异常处理器（设计文档 §2.4.7：错误响应体一律统一 APIResponse） ----------
def _error_body(code: int, msg: str) -> dict:
    return APIResponse(code=code, msg=msg, data=None, trace_id=trace_id_var.get("")).model_dump()


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Pydantic 校验失败 → 422 + code 4000。"""
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query", "path"))
    msg = f"参数校验失败: {loc} {first.get('msg', '')}".strip() if loc else "参数校验失败"
    return JSONResponse(status_code=422, content=_error_body(ERR_VALIDATION, msg))


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTPException：保持原 status，code 按 status 映射（401→4010…其余→5000）。

    business_error() 构造的 detail 已是统一 body dict 时直接透传。
    """
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        body = exc.detail
    else:
        code = HTTP_STATUS_ERROR_CODE.get(exc.status_code, ERR_INTERNAL)
        msg = exc.detail if isinstance(exc.detail, str) else "请求失败"
        body = _error_body(code, msg)
    return JSONResponse(
        status_code=exc.status_code,
        content=body,
        headers=dict(exc.headers) if exc.headers else None,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """未捕获异常 → 5xx；Neo4j/pgvector 按契约发射 5001/5002，其余 5000。

    保留 traceback 便于排错。图库与向量库异常单独映射（设计文档 §2.4.7），
    让前端能区分「依赖服务故障」与「通用内部错误」。
    """
    if isinstance(exc, GqlError):
        # GqlError 覆盖 Neo4jError 与 DriverError（ServiceUnavailable/SessionExpired 等）
        logger.exception("Neo4j 查询失败: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=ERROR_HTTP_STATUS[ERR_NEO4J],
            content=_error_body(ERR_NEO4J, "图数据库查询失败"),
        )
    if isinstance(exc, PgvectorUnavailableError):
        logger.exception("pgvector 查询失败: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=ERROR_HTTP_STATUS[ERR_PGVECTOR],
            content=_error_body(ERR_PGVECTOR, "向量检索异常"),
        )
    logger.exception("未捕获异常: %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content=_error_body(ERR_INTERNAL, "服务器内部错误"))


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


# ---------- 前端静态资源（生产：同端口托管；开发：Vite dev server 独立） ----------
frontend_dist = Path(settings.frontend_dist_dir)
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
