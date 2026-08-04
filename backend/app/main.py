"""FastAPI 入口。

启动：uv run uvicorn app.main:app --reload --port 8000
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.middleware import setup_middleware

# 应用内 logger（auth/admin 等模块）输出到标准输出，便于运行排错；
# root 默认 WARNING 会吞掉模块 INFO 日志，故启动时统一配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时
    if settings.is_production:
        if settings.secret_key == "change-me-in-production":
            raise RuntimeError("SECRET_KEY 未修改，生产环境拒绝启动")
    _prewarm_semantic()
    yield
    # 关闭时 — 资源由各自模块管理


def _prewarm_semantic() -> None:
    """后台预加载 SBERT 模型，避免首次匹配请求触发模型加载（>30s 超时）。

    模型加载约 5-15s，放后台线程执行，不阻塞 API 启动；失败静默
    （语义不可用时匹配自动降级纯规则，见 semantic.SemanticUnavailableError）。
    """
    import threading

    from app.services.matching.semantic import SkillEmbedder

    threading.Thread(target=SkillEmbedder.get().preload, daemon=True).start()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url=None,
)

setup_middleware(app)

# ---------- API 路由 ----------
from app.api.v1 import router as v1_router

app.include_router(v1_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


# ---------- 前端静态资源（生产：同端口托管；开发：Vite dev server 独立） ----------
frontend_dist = Path(settings.frontend_dist_dir)
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
