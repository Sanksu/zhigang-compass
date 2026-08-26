"""动态别名表只读接口（方案① 补齐前端：skill_aliases 列表）。

- GET /admin/skill-aliases：别名回写记录分页列表（status 过滤，倒序）。
  approved 行即 normalize_skill 并查生效源（词典→动态→白名单读序）。

只读，不触发写操作（写入方=propose 脚本 pending + approve 端点 approved）。
"""

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import SkillAlias

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
) -> dict:
    """动态别名表分页列表（倒序；approved 行为 normalize_skill 生效源）。"""
    from app.core.database import async_session_factory

    async with async_session_factory() as session:
        rows = await query_aliases(session, status, limit, offset)
        count_stmt = select(func.count()).select_from(SkillAlias)
        if status:
            count_stmt = count_stmt.where(SkillAlias.status == status)
        total = await session.scalar(count_stmt) or 0
    return {
        "items": [serialize_alias(r) for r in rows],
        "total": total, "limit": limit, "offset": offset,
    }
