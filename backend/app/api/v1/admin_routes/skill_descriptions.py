"""技能解释管理：列表 / 编辑 / 触发 LLM 补齐（admin 技能页数据源）。

解释优先级：SkillDescription（DB 覆盖）> 内置词典 SKILL_DESCRIPTIONS > 模板。
- GET /admin/skill-descriptions：列出技能及其解释来源（DB 覆盖 / 内置 / 空）
- PUT /admin/skill-descriptions/{skill_name}：人工编辑（写 DB，source=manual）
- POST /admin/skill-descriptions/backfill：对无解释技能批量用 LLM 生成（source=llm）

父级 /admin 已统一 require_permission("admin:*")，本路由不重复鉴权。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, neo4j_driver
from app.models.business import SkillAlias, SkillDescription
from app.schemas.common import error, ok
from app.services.extraction.dictionary import SKILL_WHITELIST
from app.services.extraction.llm_provider import LLMProviderChain
from app.services.graph import repository
from app.services.graph.skill_descriptions import SKILL_DESCRIPTIONS

router = APIRouter()

BACKFILL_PROMPT = (
    "你是一名技术技能词典编写者。请用一句简洁的中文解释技能「{skill}」的含义与典型用途，"
    "面向求职者阅读，不要编造来源、不要提及 JD。只返回一段纯文本，不换行。"
)


def _call_desc_llm(chain: LLMProviderChain, skill: str) -> str:
    """用给定链为单个技能生成解释文本（裸文本路由，不经结构化校验）。

    原实现套 `SkillDescReply` 结构化模型：模型返回纯文本而未发 tool call 时，
    instructor 会把文本判为空列表导致校验失败（已实测 5 例），而解释本就是
    一段自由文本，改走 `call_text_sync` 直接取文本，规避该误报。
    """
    reply = chain.call_text_sync(BACKFILL_PROMPT.format(skill=skill))
    return (reply or "").strip()


async def _all_desc_overrides(db: AsyncSession) -> dict[str, str]:
    rows = await db.execute(select(SkillDescription))
    return {r.skill_name: r.description for r in rows.scalars().all()}


async def _upsert(db: AsyncSession, skill_name: str, description: str, source: str) -> None:
    stmt = pg_insert(SkillDescription).values(
        skill_name=skill_name, description=description, source=source
    )
    update = {
        "description": description,
        "source": source,
    }
    await db.execute(stmt.on_conflict_do_update(
        index_elements=[SkillDescription.skill_name],
        set_=update,
    ))
    await db.commit()


@router.get("/skill-descriptions")
async def list_skill_descriptions(
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """技能解释列表：每项含技能名、可选覆盖(DB)与内置词典解释。"""
    overrides = await _all_desc_overrides(db)
    # 枚举与技能治理列表(/admin/skills)对齐：图技能名 ∪ 白名单标准名 ∪ approved 别名标准名，
    # 保证治理页可见的技能都能编辑解释并回显（此前仅图技能名，治理页部分技能永远缺映射）。
    all_skills = repository.query_all_skills(neo4j_driver)  # [(id, name)] 同步函数
    names = {n for _, n in all_skills}
    names.update(k for k in SKILL_WHITELIST)
    approved = await db.execute(
        select(SkillAlias.standard_name).where(SkillAlias.status == "approved")
    )
    names.update(r[0] for r in approved if r[0])
    names = sorted(names)
    if q:
        names = [n for n in names if q.lower() in n.lower()]
    items = []
    for name in names[offset: offset + limit]:
        db_desc = overrides.get(name)
        builtin = SKILL_DESCRIPTIONS.get(name)
        items.append({
            "skill_name": name,
            "override_desc": db_desc,
            "builtin_desc": builtin,
            "source": "db" if db_desc is not None else "builtin" if builtin else "",
        })
    return ok(data={"total": len(names), "offset": offset, "limit": limit, "items": items})


class PutSkillDescBody(BaseModel):
    description: str


@router.put("/skill-descriptions/{skill_name}")
async def update_skill_description(
    skill_name: str,
    body: PutSkillDescBody,
    db: AsyncSession = Depends(get_db),
):
    """保存某技能的解释（人工编辑，写 DB 覆盖）。"""
    desc = body.description.strip()
    if not desc:
        return error(0, "解释不能为空")
    await _upsert(db, skill_name, desc, "manual")
    return ok(data={"skill_name": skill_name, "description": desc, "source": "manual"})


@router.post("/skill-descriptions/backfill")
async def backfill_skill_descriptions(
    limit: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """触发 LLM 补齐：对无解释（无 DB 覆盖）的技能批量生成描述（source=llm）。

    仅处理 limit 个空缺技能以控制成本；前端可分页多次触发或调整 limit。
    """
    overrides = await _all_desc_overrides(db)
    all_skills = repository.query_all_skills(neo4j_driver)  # 同步函数
    missing = sorted({n for _, n in all_skills if n not in overrides and n not in SKILL_DESCRIPTIONS})
    if not missing:
        return ok(data={"generated": 0, "total_missing": 0, "message": "无空缺解释"})

    done = 0
    failed = 0
    try:
        chain = LLMProviderChain()
    except Exception:
        return error(0, "LLM 未配置，无法补齐")

    for name in missing[:limit]:
        try:
            text = _call_desc_llm(chain, name)
            if not text:
                failed += 1
                continue
            await _upsert(db, name, text, "llm")
            done += 1
        except Exception:
            failed += 1

    return ok(data={
        "generated": done,
        "failed": failed,
        "total_missing": len(missing),
        "limit": limit,
    })