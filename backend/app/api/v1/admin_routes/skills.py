"""技能治理列表（endpoint /admin/skills，RBAC admin only）。

技能治理页数据源（只读，不写词典/图谱/白名单）：
- configs/skill_whitelist.yaml 的标准技能名（name→category，幻觉防控第三道防线单一事实源）
- 动态别名表 approved 行（variant→standard_name，normalize_skill 生效源）

每条聚合为：{name, category, in_whitelist, is_noise, aliases[]}，支持 q /
category / 白名单态 / 噪声态过滤与分页。白名单/分类的增改删为业务敏感操作，
本期只读，后续单独 PR 走人工把关。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.database import get_db
from app.models.business import SkillAlias
from app.schemas.common import ok
from app.services.extraction.dictionary import (
    SKILL_CATEGORY,
    SKILL_WHITELIST,
    is_noise_skill,
)

router = APIRouter(tags=["admin-skills"])


def build_skill_admin_page(
    approved_aliases: list[tuple[str, str]],
    q: str = "",
    category: str = "",
    whitelist: str = "all",
    noise: str = "all",
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict], int]:
    """纯函数：别名行 → 去重技能列表 → 过滤 → 分页。供端点与测试复用。"""
    skills: dict[str, dict] = {}
    for name in SKILL_WHITELIST:
        skills[name] = {
            "name": name,
            "category": SKILL_CATEGORY.get(name) or "",
            "in_whitelist": True,
            "is_noise": is_noise_skill(name),
            "aliases": [],
        }
    for variant, standard in approved_aliases:
        standard = (standard or "").strip()
        variant = (variant or "").strip()
        if not standard:
            continue
        entry = skills.get(standard)
        if entry is None:
            entry = {
                "name": standard,
                "category": SKILL_CATEGORY.get(standard) or "",
                "in_whitelist": standard in SKILL_WHITELIST,
                "is_noise": is_noise_skill(standard),
                "aliases": [],
            }
            skills[standard] = entry
        if variant and variant not in entry["aliases"]:
            entry["aliases"].append(variant)

    ql = q.strip().lower()
    items = []
    for name in skills:
        entry = skills[name]
        if ql and ql not in name.lower() and not any(ql in a.lower() for a in entry["aliases"]):
            continue
        if category and category.lower() not in entry["category"].lower():
            continue
        if whitelist == "only" and not entry["in_whitelist"]:
            continue
        if whitelist == "exclude" and entry["in_whitelist"]:
            continue
        if noise == "only" and not entry["is_noise"]:
            continue
        if noise == "exclude" and entry["is_noise"]:
            continue
        items.append(entry)

    items.sort(key=lambda e: e["name"])
    total = len(items)
    start = (page - 1) * size
    paged = items[start:start + size]
    return paged, total


@router.get("/skills")
async def list_skills(
    q: str = Query(default="", max_length=200),
    category: str = Query(default="", max_length=100),
    whitelist: str = Query(default="all", pattern="^(all|only|exclude)$"),
    noise: str = Query(default="all", pattern="^(all|only|exclude)$"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """技能治理列表（白名单标准名 ∪ approved 别名标准名；q/分类/白名单/噪声过滤）。"""
    rows = (await db.execute(
        select(SkillAlias.variant, SkillAlias.standard_name).where(SkillAlias.status == "approved")
    )).all()
    approved = [(str(r[0]), str(r[1])) for r in rows]
    items, total = build_skill_admin_page(approved, q, category, whitelist, noise, page, size)
    return ok(data={"total": total, "page": page, "size": size, "items": items})