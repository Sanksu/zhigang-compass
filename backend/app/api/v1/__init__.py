"""v1 路由聚合。"""

from fastapi import APIRouter

from app.api.v1 import admin, auth, discovery, evolution, graph, match, resume

router = APIRouter()

router.include_router(auth.router, prefix="/auth", tags=["认证"])
router.include_router(graph.router, prefix="/graph", tags=["图谱"])
router.include_router(match.router, prefix="/match", tags=["匹配"])
router.include_router(resume.router, prefix="/resume", tags=["简历"])
router.include_router(evolution.router, prefix="/evolution", tags=["演化"])
router.include_router(discovery.router, prefix="/discovery", tags=["发现"])
router.include_router(admin.router, prefix="", tags=["管理"])
