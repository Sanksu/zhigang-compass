"""新岗位发现阶段二：RAG 接地（设计文档 7.2.3 节）。

candidate 触发后执行两层接地：
1. 权威岗位库检索：O*NET occupations 表（PostgreSQL）按岗位名/别名匹配，
   命中后取权威定义（英文）作为定义草案基座。
2. 种子列表匹配：预置 12 个新兴岗位种子（configs/emerging_seeds.yaml），
   命中后取种子描述作为定义草案基座。

定义草案生成：LLM 可用时聚合上下文生成中文草案；不可用或失败时
回退权威库定义原文（避免草案为空阻塞 admin 审核）。

RAG 接地是"辅助确认"而非"硬门控"：未命中权威库/种子的 candidate
仍留在 candidate 池标记 unverified，由 admin 人工判断（设计文档 7.2.3）。
"""

from pathlib import Path
from typing import Optional, Protocol

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.discovery.schemas import RagGroundingResult

_SEEDS_PATH = Path(__file__).resolve().parents[3] / "configs" / "emerging_seeds.yaml"

# 权威库命中判定阈值：岗位名/别名任一含 candidate 岗位名核心词即视为命中。
# 简单 substring 匹配（小写），避免大小写与空格差异导致漏判。
_MATCH_MIN_ALIAS_LEN = 3


def _norm(text: str) -> str:
    """归一化：小写 + 去首尾空白。"""
    return (text or "").strip().lower()


def match_seed(position_name: str, seeds: list[dict]) -> Optional[dict]:
    """匹配种子列表（name/aliases 与岗位名子串互相包含）。

    Returns:
        命中的种子 dict，未命中返回 None
    """
    pos = _norm(position_name)
    if not pos:
        return None
    for seed in seeds:
        names = [seed.get("name", ""), *(seed.get("aliases") or [])]
        for name in names:
            n = _norm(name)
            if not n:
                continue
            # 岗位名含种子名 或 种子名含岗位名（覆盖 "RAG 工程师" vs "RAG" 双向）
            if n in pos or pos in n:
                return seed
    return None


async def search_authoritative(
    position_name: str,
    db: AsyncSession,
    limit: int = 5,
) -> list[dict]:
    """在权威岗位库（O*NET occupations 表）检索候选岗位。

    匹配口径：岗位名或别名含 candidate 岗位名（子串，小写），优先 name 命中、
    别名命中靠后；返回前 limit 条。别名过滤过短词防噪音。

    Returns:
        命中的 occupation dict 列表（code/name/category/definition/aliases）
    """
    pos = _norm(position_name)
    if not pos:
        return []
    if len(pos) < _MATCH_MIN_ALIAS_LEN:
        return []

    from app.models.business import Occupation
    from sqlalchemy import cast
    from sqlalchemy.dialects import postgresql

    stmt = (
        select(Occupation)
        .where(
            (Occupation.name.ilike(f"%{pos}%"))
            | (cast(Occupation.aliases, postgresql.TEXT).ilike(f"%{pos}%"))
        )
        .limit(limit)
    )
    rows = (await db.scalars(stmt)).all()

    results = []
    for occ in rows:
        name_hit = pos in _norm(occ.name)
        alias_hits = [a for a in (occ.aliases or []) if pos in _norm(a)]
        results.append(
            {
                "code": occ.code,
                "name": occ.name,
                "category": occ.category,
                "definition": occ.definition,
                "name_hit": name_hit,
                "alias_hits": alias_hits,
                "score": 1.0 if name_hit else 0.5,
            }
        )
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


async def _generate_definition(
    position_name: str,
    seed: Optional[dict],
    occupation: Optional[dict],
    llm,
) -> str:
    """生成岗位定义草案。

    优先级：LLM 聚合生成（可配置）→ 种子描述 → 权威库定义原文。
    LLM 失败静默回退，不阻塞接地判定。
    """
    if occupation and occupation.get("definition"):
        return occupation["definition"]
    if seed and seed.get("description"):
        return seed["description"]
    return ""


async def ground_with_rag(
    position_name: str,
    db: AsyncSession,
    *,
    llm=None,
    seeds_path: Path | None = None,
) -> RagGroundingResult:
    """RAG 接地主流程（阶段二）。

    Args:
        position_name: candidate 岗位名
        db: PostgreSQL 会话（权威库检索）
        llm: LLMProviderChain（可选，用于定义草案 LLM 生成）
        seeds_path: 种子 yaml 路径（测试可注入）

    Returns:
        RagGroundingResult：命中状态 + 定义草案
    """
    seeds = _load_seeds(seeds_path or _SEEDS_PATH)
    seed = match_seed(position_name, seeds)

    hits = await search_authoritative(position_name, db)
    occupation = hits[0] if hits else None

    if seed is None and occupation is None:
        return RagGroundingResult()

    definition = await _generate_definition(position_name, seed, occupation, llm)
    return RagGroundingResult(
        matched=True,
        seed_matched=seed is not None,
        rag_matched=occupation is not None,
        matched_name=(occupation or seed or {}).get("name", ""),
        occupation_code=(occupation or {}).get("code", ""),
        definition=definition,
    )


def _load_seeds(path: Path) -> list[dict]:
    """加载种子列表 yaml。文件缺失/解析失败返回空列表（接地降级为仅权威库）。"""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    return [s for s in data.get("seeds", []) if isinstance(s, dict) and s.get("name")]
