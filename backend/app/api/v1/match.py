"""匹配路由：自动推荐、人岗比对。"""

from fastapi import APIRouter

from app.schemas.common import error

router = APIRouter()


@router.post("/recommend")
async def recommend():
    """自动推荐 Top-N 岗位（ARQ 异步任务）。"""
    return error(501, "待实现")


@router.post("/compare")
async def compare():
    """人岗比对：五维雷达图 + 差距分析 + 学习路径。"""
    return error(501, "待实现")
