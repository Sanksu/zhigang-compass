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

from app.api.common import resolve_operator
from app.api.deps import require_permission
from app.core.database import get_db, neo4j_driver, redis_client
from app.core.errors import ERR_INTERNAL, ERR_VALIDATION
from app.models.business import AuditLog, SkillAlias, SkillDescription
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


async def _all_desc_names(db: AsyncSession) -> set[str]:
    """图技能名 ∪ 白名单标准名 ∪ approved 别名标准名。

    list 与 backfill 共享同一枚举，保证列表可见的空解释技能都能被 LLM 补齐
    （此前 backfill 仅扫图技能名，白名单/别名中的空缺技能列表可见但永补不上）。
    """
    all_skills = repository.query_all_skills(neo4j_driver)  # 同步函数 [(id, name)]
    names = {n for _, n in all_skills}
    names.update(k for k in SKILL_WHITELIST)
    approved = await db.execute(
        select(SkillAlias.standard_name).where(SkillAlias.status == "approved")
    )
    names.update(r[0] for r in approved if r[0])
    return names


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


async def _invalidate_portrait_caches() -> None:
    """失效技能解释相关的岗位画像视图缓存（graph:view:positionPortrait:*）。

    技能解释编辑/LLM 补齐后，岗位画像侧栏的技能说明需即时刷新；此前仅靠
    positionPortrait 缓存的 30s TTL 兜底，编辑后画面滞后。精确匹配 positionPortrait
    前缀（不误伤其他图视图缓存），动作低频无压力。
    """
    keys = [key async for key in redis_client.scan_iter(match="graph:view:positionPortrait:*")]
    if keys:
        await redis_client.delete(*keys)


@router.get("/skill-descriptions")
async def list_skill_descriptions(
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """技能解释列表：每项含技能名、可选覆盖(DB)与内置词典解释。"""
    overrides = await _all_desc_overrides(db)
    # 枚举与技能治理列表(/admin/skills)对齐（图技能名 ∪ 白名单标准名 ∪ approved 别名标准名），
    # 保证治理页可见的技能都能编辑解释并回显；与 backfill 同一枚举（_all_desc_names）。
    names = await _all_desc_names(db)
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
    current_user: dict = Depends(require_permission("admin:*")),
):
    """保存某技能的解释（人工编辑，写 DB 覆盖 + 审计留痕）。"""
    operator, err = resolve_operator(current_user)
    if err is not None:
        return err
    desc = body.description.strip()
    if not desc:
        return error(ERR_VALIDATION, "解释不能为空")
    await _upsert(db, skill_name, desc, "manual")
    db.add(
        AuditLog(
            user_id=operator,
            action="admin.skill_description.update",
            resource="SkillDescription",
            resource_id=skill_name,
            detail={"description": desc, "source": "manual"},
        )
    )
    await db.commit()
    await _invalidate_portrait_caches()  # 编辑后岗位画像技能说明即时刷新
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
    names = await _all_desc_names(db)
    missing = sorted(
        n for n in names if n not in overrides and n not in SKILL_DESCRIPTIONS
    )
    if not missing:
        return ok(data={"generated": 0, "total_missing": 0, "message": "无空缺解释"})

    done = 0
    failed = 0
    try:
        chain = LLMProviderChain()
    except Exception:
        return error(ERR_INTERNAL, "LLM 未配置，无法补齐")

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

    if done > 0:
        await _invalidate_portrait_caches()  # 补齐后岗位画像技能说明即时刷新

    return ok(data={
        "generated": done,
        "failed": failed,
        "total_missing": len(missing),
        "limit": limit,
    })