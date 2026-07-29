"""简历路由：上传、解析、PII 脱敏。"""

from fastapi import APIRouter

from app.schemas.common import error

router = APIRouter()


@router.post("/parse")
async def parse_resume():
    """简历解析（ARQ 异步任务，支持 PDF/Word/图片/扫描件）。"""
    return error(501, "待实现")


@router.get("/task/{task_id}")
async def task_status(task_id: str):
    """轮询异步任务状态。"""
    return error(501, "待实现")
