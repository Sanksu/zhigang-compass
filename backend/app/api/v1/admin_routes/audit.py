"""管理后台审计日志路由（RBAC admin only）。

对齐契约 /api/v1/admin/audit/logs：分页 + 类别过滤。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import iso, paged_ok, paginate
from app.core.database import get_db
from app.models.business import AuditLog

router = APIRouter()


@router.get("/audit/logs")
async def audit_logs(
    category: str | None = Query(default=None, pattern="^(AUTH|GRAPH|DATA|ADMIN)$"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """审计日志查询（分页 + 类别过滤）。"""
    stmt = select(AuditLog)
    count_stmt = select(func.count()).select_from(AuditLog)
    if category:
        # action 以模块前缀命名（如 auth.login / admin.user.update），按前缀过滤
        prefix = category.lower() + "%"
        stmt = stmt.where(AuditLog.action.like(prefix))
        count_stmt = count_stmt.where(AuditLog.action.like(prefix))
    rows, total = await paginate(
        db, stmt, page, size, count_stmt=count_stmt
    )
    items = [
        {
            "id": log.id,
            "user_id": log.user_id,
            "action": log.action,
            "resource": log.resource,
            "resource_id": log.resource_id,
            "detail": log.detail,
            "ip_address": log.ip_address,
            "created_at": iso(log.created_at),
        }
        for log in rows
    ]
    return paged_ok(items, total, page, size)
