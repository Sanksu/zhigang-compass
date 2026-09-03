"""技能治理：标记/取消噪声（RBAC admin only）。

动态噪声层（skill_filters_dynamic.json）已有 add_entry/remove_entry。
- PUT /admin/skills/{name}/noise：{noise:true/false} → 写入 blocked 动态层并
  写一条 LLM 决策记录（domain=skill_noise，供决策验收台展示）；即时生效。

父级 /admin 已统一 require_permission，本路由显式声明以匹配同类治理路由。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.common import resolve_operator
from app.api.deps import require_permission
from app.core.database import async_session_factory
from app.models.business import LLMDecisionRecord
from app.schemas.common import error, ok
from app.services.extraction.dynamic_filters import add_entry, remove_entry
from app.services.llm_decision import DOMAIN_SKILL_NOISE

router = APIRouter(tags=["admin-skill-noise"])


class SkillNoiseBody(BaseModel):
    noise: bool


@router.put("/skills/{name}/noise")
async def set_skill_noise(
    name: str,
    body: SkillNoiseBody,
    current_user: dict = Depends(require_permission("admin:*")),
):
    """标记/取消某技能为噪声（写动态 blocked 层 + skill_noise 决策记录）。"""
    operator, err = resolve_operator(current_user)
    if err is not None:
        return err
    term = name.strip()
    if not term:
        return error(0, "技能名不能为空")
    if body.noise:
        add_entry("blocked", term, reason="技能治理手动标记噪声", source="manual", operator=operator)
    else:
        remove_entry("blocked", term)
    async with async_session_factory() as session:
        session.add(LLMDecisionRecord(
            domain=DOMAIN_SKILL_NOISE,
            entity_type="skill",
            entity_id=term,
            structured_output={"name": term, "noise": body.noise},
            status="approved",
            effects_applied=True,
        ))
        await session.commit()
    return ok(data={"name": term, "noise": body.noise})