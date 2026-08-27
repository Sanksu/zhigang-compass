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
from starlette.exceptions import HTTPException as StarletteHTTPException
from neo4j.exceptions import GqlError

from app.core.config import settings
from app.core.security import ensure_jwt_keys
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
    # JWT RS256 密钥预加载（所有环境）：密钥经 compose 挂载注入而非入镜像，
    # 挂载缺失时懒加载会把 FileNotFoundError 暴露成运行时 500（登录/refresh
    # 报「服务器内部错误」，2026-08-22 事故）——启动即校验并给出修复指引
    ensure_jwt_keys()
    if settings.is_production:
        if settings.secret_key == "change-me-in-production":
            raise RuntimeError("SECRET_KEY 未修改，生产环境拒绝启动")
        if settings.admin_password == "admin123":
            raise RuntimeError("ADMIN_PASSWORD 仍为默认弱口令，生产环境拒绝启动")
        # H2 修复:生产姿态校验补全。debug=True 默认值会让 asyncpg echo 把含
        # 简历 PII/密码哈希列的 SQL 全量打进日志(§六 H2);CORS 通配 * 允许任意
        # 站点跨域携带凭据访问。两项漏配时 fail-fast 拒绝启动。
        if settings.debug:
            raise RuntimeError(
                "生产环境禁止 DEBUG=True（SQL echo 将泄露 PII 至日志），"
                "请显式设置 DEBUG=false"
            )
        if "*" in settings.cors_origins:
            raise RuntimeError(
                "生产环境禁止 CORS 通配 *，请显式配置 cors_origins 白名单"
            )
    # 动态别名表启动加载（方案①，第六轮审查 P0-1）：approve 端点的即时刷新
    # 只覆盖 API 进程内存；此处保证 API 重启后缓存非空（内部 fail-soft+warning）
    from app.services.extraction.dictionary import refresh_dynamic_aliases

    loaded_aliases = await refresh_dynamic_aliases()
    logger.info("动态别名表启动加载完成：%d 条", loaded_aliases)
    await _prewarm_semantic()
    yield
    await _shutdown_resources()


async def _shutdown_resources() -> None:
    """关闭时释放连接资源（各自身 try/except 防单点失败阻断后续关闭）。

    08-17 P2 迁移新增 async_neo4j_driver，一并纳入关闭路径；sync neo4j_driver
    仍由停机遇期关闭（workers/脚本复用）。PostgreSQL asyncpg 连接池 dispose 常驻
    服务关闭时回收（单元测试不跑 lifespan，不会影响测试套件）。
    """
    from app.core import database as _db

    try:
        await _db.async_neo4j_driver.close()
    except Exception:
        logger.exception("async_neo4j_driver 关闭失败")
    try:
        _db.neo4j_driver.close()
    except Exception:
        logger.exception("neo4j_driver 关闭失败")
    try:
        await _db.redis_client.aclose()
    except Exception:
        logger.exception("redis_client 关闭失败")
    try:
        await _db.engine.dispose()
    except Exception:
        logger.exception("PostgreSQL 连接池 dispose 失败")


async def _prewarm_semantic() -> None:
    """启动时同步预加载 SBERT 模型（08-15 修复：比对详情白屏）。

    此前后台线程预加载——api 冷启动后首次 compare 实测 16.3s（模型加载 +
    编码），用户感知"加载比对详情…白屏一段时间"。改为启动阶段同步等待
    （实测 16s 在 healthcheck start_period 窗口内），用户请求永不感知加载。
    失败静默（语义不可用时匹配自动降级纯规则，见 semantic.SemanticUnavailableError）。

    M8 修复:静默语义保留,但补 warning 日志恢复可观测——SBERT 加载失败
    唯一症状是线上匹配莫名变弱,无日志则排障无线索。
    """
    import asyncio

    from app.services.matching.semantic import SkillEmbedder

    try:
        await asyncio.to_thread(SkillEmbedder.get().preload)
        # 预热岗位画像（08-15 修复：首次 compare 的 positions 加载 +
        # 批量 encode 实测 16s——比模型加载更长，用户"比对详情白屏"主因）。
        # P1 起走 Redis 版本化共享缓存：冷启动构建载荷并切指针，
        # 其他进程/worker 直接读共享载荷，不再各自全量查图。
        from app.services.matching.shared_cache import load_positions_shared

        await load_positions_shared()
    except Exception:
        logger.warning(
            "语义模型预热失败,匹配已降级为纯规则模式(详见 SemanticUnavailableError)",
            exc_info=True,
        )


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
class _SPAFallbackStaticFiles(StaticFiles):
    """SPA history 路由回退（08-15 修复：前端路由刷新 404）。

    StaticFiles(html=True) 只回退目录索引，/evolution 等前端路由直接
    刷新时 404 {"detail":"Not Found"}。本类将非 /api 路径的 404 回退到
    index.html（前端 Router 接管渲染）；/api/* 404 保持原样（契约 JSON）。
    """

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Starlette 1.3 静态文件未命中 raise HTTPException(404)（非返回 404 响应）；
            # 注意用 starlette.exceptions.HTTPException（fastapi.HTTPException 是独立类）
            if exc.status_code == 404 and not scope.get("path", "").startswith("/api/"):
                return await super().get_response("index.html", scope)
            raise
        if response.status_code == 404 and not scope.get("path", "").startswith("/api/"):
            return await super().get_response("index.html", scope)
        return response


frontend_dist = Path(settings.frontend_dist_dir)
if frontend_dist.exists():
    app.mount(
        "/", _SPAFallbackStaticFiles(directory=str(frontend_dist), html=True),
        name="frontend",
    )
