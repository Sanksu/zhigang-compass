"""演化路由：版本回溯、Diff 对比、趋势查询。"""

from fastapi import APIRouter

from app.schemas.common import error

router = APIRouter()


@router.get("/versions")
async def list_versions():
    """版本快照列表。"""
    return error(501, "待实现")


@router.get("/diff")
async def version_diff(from_version: str, to_version: str):
    """两个版本快照 Diff 对比。"""
    return error(501, "待实现")


@router.get("/trends")
async def skill_trends(skill: str):
    """技能频次趋势。"""
    return error(501, "待实现")
