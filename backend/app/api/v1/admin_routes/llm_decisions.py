"""LLM 决策与验收只读接口（PR7：管理后台决策页数据源）。

- GET /admin/llm-decisions：决策记录分页列表（domain/status 过滤，倒序）
- GET /admin/llm-decisions/summary：按 domain×status 汇总（验收卡片）

只读，不触发任何写操作（决策记录的生产者见各域 worker/scripts）。
"""

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import LLMDecisionRecord

router = APIRouter(tags=["admin-llm-decisions"])

# 响应/列表字段（避免把内部大字段无谓外泄；structured_output 保留供抽检）
_SERIALIZE_FIELDS = (
    "id", "domain", "entity_type", "entity_id", "run_id", "env",
    "input_hash", "provider", "model", "prompt_version", "schema_version",
    "structured_output", "confidence", "gate_result", "risk_tier", "status",
    "reviewer", "review_reason", "effects_applied", "duration_ms",
    "attempts", "fallback_reason", "created_at",
)


def serialize_record(record: LLMDecisionRecord) -> dict:
    """ORM 记录 → 契约字段（created_at 转 ISO 字符串，JSON 友好）。"""
    data: dict = {}
    for field in _SERIALIZE_FIELDS:
        value = getattr(record, field, None)
        if field == "created_at" and value is not None:
            value = value.isoformat()
        data[field] = value
    return data


def build_query(domain: str, status: str, limit: int, offset: int):
    """决策列表查询（纯函数可测）：domain/status 过滤 + 倒序分页。"""
    stmt = select(LLMDecisionRecord).order_by(LLMDecisionRecord.created_at.desc())
    if domain:
        stmt = stmt.where(LLMDecisionRecord.domain == domain)
    if status:
        stmt = stmt.where(LLMDecisionRecord.status == status)
    return stmt.limit(limit).offset(offset)


async def query_decisions(
    session: AsyncSession,
    domain: str = "",
    status: str = "",
    limit: int = 50,
    offset: int = 0,
) -> list[LLMDecisionRecord]:
    """执行决策列表查询（session 由调用方提供，便于测试注入）。"""
    rows = await session.scalars(build_query(domain, status, limit, offset))
    return list(rows.all())


async def summarize(session: AsyncSession) -> dict:
    """按 domain×status 汇总（记录量小，内存聚合；供管理端卡片）。"""
    rows = (await session.scalars(select(LLMDecisionRecord))).all()
    by_domain: dict[str, dict] = {}
    totals: dict[str, int] = {"proposal": 0, "auto_applied": 0, "blocked": 0, "shadow": 0, "other": 0}
    for r in rows:
        domain_entry = by_domain.setdefault(r.domain, {"domain": r.domain, "by_status": {}, "total": 0})
        domain_entry["by_status"][r.status] = domain_entry["by_status"].get(r.status, 0) + 1
        domain_entry["total"] += 1
        key = r.status if r.status in totals else "other"
        totals[key] += 1
    totals["records"] = len(rows)
    return {
        "by_domain": sorted(by_domain.values(), key=lambda d: -d["total"]),
        "totals": totals,
    }


@router.get("/llm-decisions")
async def list_llm_decisions(
    domain: str = Query(default="", description="决策域过滤（空=全部）"),
    status: str = Query(default="", description="状态过滤：shadow/proposal/auto_applied/blocked 等（空=全部）"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """决策记录分页列表（倒序，只读）。"""
    from app.core.database import async_session_factory

    async with async_session_factory() as session:
        rows = await query_decisions(session, domain, status, limit, offset)
    return {"items": [serialize_record(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/llm-decisions/summary")
async def llm_decisions_summary() -> dict:
    """决策记录汇总（domain×status，验收卡片数据源，只读）。"""
    from app.core.database import async_session_factory

    async with async_session_factory() as session:
        return await summarize(session)