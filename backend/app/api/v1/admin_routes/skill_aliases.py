"""动态别名表只读接口（方案① 补齐前端：skill_aliases 列表）。

- GET /admin/skill-aliases：别名回写记录分页列表（status 过滤，倒序）。
  approved 行即 normalize_skill 并查生效源（词典→动态→白名单读序）。
- POST /admin/skill-aliases/{alias_id}/review：别名复核（pending→approved/rejected）
  供技能治理页人工处置，写 AuditLog；approved 后热刷新动态别名表即时生效。

只读列举；写入方=propose 脚本 pending + 复核端点为 approved/rejected。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import resolve_operator
from app.api.deps import require_permission
from app.core.database import get_db
from app.core.errors import ERR_CONFLICT, ERR_NOT_FOUND
from app.models.business import AuditLog, SkillAlias
from app.schemas.common import error, ok
from app.services.extraction.dictionary import refresh_dynamic_aliases

router = APIRouter(tags=["admin-skill-aliases"])

_SERIALIZE_FIELDS = (
    "id", "variant", "standard_name", "status", "proposal_id", "source",
    "reviewed_by", "review_reason", "confidence", "applied_to_graph", "created_at",
)


def serialize_alias(row: SkillAlias) -> dict:
    """ORM 行 → 契约字段（created_at ISO 字符串，JSON 友好）。"""
    data: dict = {}
    for field in _SERIALIZE_FIELDS:
        value = getattr(row, field, None)
        if field == "created_at" and value is not None:
            value = value.isoformat()
        data[field] = value
    return data


def build_query(status: str, limit: int, offset: int):
    """列表查询（纯函数可测）：status 过滤 + 创建时间倒序分页。"""
    stmt = select(SkillAlias).order_by(SkillAlias.created_at.desc())
    if status:
        stmt = stmt.where(SkillAlias.status == status)
    return stmt.limit(limit).offset(offset)


async def query_aliases(
    session: AsyncSession, status: str = "", limit: int = 50, offset: int = 0,
) -> list[SkillAlias]:
    """执行别名列表查询（session 由调用方注入，便于测试）。"""
    rows = await session.scalars(build_query(status, limit, offset))
    return list(rows.all())


@router.get("/skill-aliases")
async def list_skill_aliases(
    status: str = Query(default="", description="状态过滤：pending/approved/rejected（空=全部）"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """动态别名表分页列表（倒序；approved 行为 normalize_skill 生效源）。"""
    from app.api.common import ok
    from app.core.database import async_session_factory

    async with async_session_factory() as session:
        rows = await query_aliases(session, status, limit, offset)
        count_stmt = select(func.count()).select_from(SkillAlias)
        if status:
            count_stmt = count_stmt.where(SkillAlias.status == status)
        total = await session.scalar(count_stmt) or 0
    # 契约（openapi /admin/skill-aliases 200）声明 ApiResponse 包装——第六轮
    # 审查 P0-2：此前裸返 {items,...}，前端 apiGet 取 res.data.data 得 undefined
    return ok({
        "items": [serialize_alias(r) for r in rows],
        "total": total, "limit": limit, "offset": offset,
    })


class SkillAliasReviewIn(BaseModel):
    """POST /admin/skill-aliases/{alias_id}/review 请求体。"""

    approved: bool
    reason: str = Field(default="", max_length=500)


@router.post("/skill-aliases/{alias_id}/review")
async def review_skill_alias(
    alias_id: str,
    body: SkillAliasReviewIn,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """别名复核：pending → approved/rejected。写 AuditLog；approved 后热刷新动态别名表。"""
    operator, err = resolve_operator(current_user)
    if err is not None:
        return err
    row = (await db.execute(
        select(SkillAlias).where(SkillAlias.id == alias_id)
    )).scalar_one_or_none()
    if row is None:
        return error(ERR_NOT_FOUND, "别名不存在", http_status=404)
    if row.status != "pending":
        return error(ERR_CONFLICT, f"仅 pending 可复核，当前 {row.status}", http_status=409)
    row.status = "approved" if body.approved else "rejected"
    row.reviewed_by = operator
    row.review_reason = body.reason
    db.add(AuditLog(
        user_id=operator,
        action="admin.skill_alias.review",
        resource="skill_aliases",
        resource_id=str(row.id),
        detail={"approved": body.approved, "variant": row.variant, "standard_name": row.standard_name},
    ))
    await db.commit()
    if body.approved:
        await refresh_dynamic_aliases()
    return ok(data={"id": str(row.id), "status": row.status, "approved": body.approved})
