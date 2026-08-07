"""新岗位发现阶段二：RAG 接地（设计文档 7.2.3 节）。

candidate 触发后执行两层接地：
1. 权威岗位库检索：occupations 三源（O*NET / 人社部大典 / LinkedIn，
   同一表 source 字段区分；PostgreSQL 语义向量 + Neo4j 全文双路），
   命中后取权威定义（英文）作为定义草案基座。
2. 种子列表匹配：预置 12 个新兴岗位种子（configs/emerging_seeds.yaml），
   命中后取种子描述作为定义草案基座。

权威库双路检索（§7.2.3 原文）：Neo4j 全文索引（关键词 top-10）+ pgvector
向量检索（语义 top-10），合并去重。降级链：语义路（向量列/扩展/模型任一
不可用）→ 跳过；关键词路（Neo4j 未同步/不可用）→ PostgreSQL ILIKE。

定义草案生成：LLM 可用时聚合上下文生成中文草案；不可用或失败时
回退权威库定义原文（避免草案为空阻塞 admin 审核）。

RAG 接地是"辅助确认"而非"硬门控"：未命中权威库/种子的 candidate
仍留在 candidate 池标记 unverified，由 admin 人工判断（设计文档 7.2.3）。
"""

import asyncio
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.discovery.schemas import RagGroundingResult

_SEEDS_PATH = Path(__file__).resolve().parents[3] / "configs" / "emerging_seeds.yaml"

# 权威库命中判定阈值：岗位名/别名任一含 candidate 岗位名核心词即视为命中。
# 简单 substring 匹配（小写），避免大小写与空格差异导致漏判。
_MATCH_MIN_ALIAS_LEN = 3

# Neo4j 全文查询（Lucene 语法）特殊字符：查询前剔除，避免语法异常
_LUCENE_SPECIAL = frozenset('+-&|!(){}[]^"~*?:\\/')


def _norm(text: str) -> str:
    """归一化：小写 + 去首尾空白。"""
    return (text or "").strip().lower()


def _sanitize_fulltext(q: str) -> str:
    """剔除 Neo4j 全文查询的 Lucene 特殊字符，空串视为无关键词命中。"""
    return "".join(ch for ch in q if ch not in _LUCENE_SPECIAL).strip()


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


def _normalize_hit(code, name, category, definition, aliases, pos, score, source):
    """统一命中结构（语义/关键词两路同一 shape，供前端/定义草案消费）。

    score：语义路为余弦相似度，关键词路为全文分数或 name/别名命中分。
    """
    name_hit = pos in _norm(name)
    alias_hits = [a for a in (aliases or []) if pos in _norm(a)]
    return {
        "code": code,
        "name": name,
        "category": category,
        "definition": definition,
        "name_hit": name_hit,
        "alias_hits": alias_hits,
        "score": score,
        "source": source,
    }


def _merge_hits(hits: list[dict], limit: int) -> list[dict]:
    """按 code 合并去重（保留高分），按 score 降序，取前 limit 条。"""
    best: dict[str, dict] = {}
    for h in hits:
        cur = best.get(h["code"])
        if cur is None or h["score"] > cur["score"]:
            best[h["code"]] = h
    return sorted(best.values(), key=lambda h: h["score"], reverse=True)[:limit]


async def _pg_ilike(db: AsyncSession, pos: str, limit: int) -> list[dict]:
    """PostgreSQL ILIKE 关键词检索（Neo4j 全文路的降级兜底）。"""
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
    return [
        _normalize_hit(
            occ.code, occ.name, occ.category, occ.definition, occ.aliases,
            pos, 1.0 if pos in _norm(occ.name) else 0.5, "keyword",
        )
        for occ in rows
    ]


async def _neo4j_fulltext(neo4j, pos: str, limit: int) -> list[dict]:
    """Neo4j occupation_search 全文索引关键词检索（设计 7.2.3 双路之一）。

    neo4j 为驱动对象（.session() 上下文）。查询前剔除 Lucene 特殊字符；
    任一异常返回 []（由调用方降级 PostgreSQL ILIKE）。
    """
    q = _sanitize_fulltext(pos)
    if not q:
        return []
    try:
        with neo4j.session() as session:
            rows = session.run(
                "CALL db.index.fulltext.queryNodes('occupation_search', $q) "
                "YIELD node, score "
                "RETURN node.code AS code, node.name AS name, "
                "node.category AS category, node.definition AS definition, "
                "node.aliases AS aliases, score "
                "LIMIT $limit",
                q=q,
                limit=limit,
            ).data()
    except Exception:
        # Neo4j 未同步/不可达：降级 ILIKE，不阻塞接地
        return []
    return [
        _normalize_hit(
            r["code"], r["name"], r.get("category") or "",
            r.get("definition") or "", r.get("aliases") or [],
            pos, float(r["score"]), "keyword",
        )
        for r in rows
        if r.get("code")
    ]


async def _keyword_search(neo4j, db: AsyncSession, pos: str, limit: int) -> list[dict]:
    """关键词路：Neo4j 全文优先，未命中/不可用时降级 PostgreSQL ILIKE。"""
    if neo4j is not None:
        hits = await _neo4j_fulltext(neo4j, pos, limit)
        if hits:
            return hits
    return await _pg_ilike(db, pos, limit)


async def _semantic_search(db: AsyncSession, pos: str, embedder, limit: int) -> list[dict]:
    """pgvector 语义检索：occupations.embedding 余弦相似度 Top-k（设计 7.2.3）。

    embedder 为 None（未注入）时跳过语义路；模型不可用抛
    SemanticUnavailableError，由调用方捕获降级为关键词路。
    """
    if embedder is None:
        return []
    from app.models.business import Occupation

    qvec = embedder.embed(pos)
    stmt = (
        select(Occupation)
        .order_by(Occupation.embedding.cosine_distance(qvec))
        .limit(limit)
    )
    rows = (await db.scalars(stmt)).all()
    return [
        _normalize_hit(
            occ.code, occ.name, occ.category, occ.definition, occ.aliases,
            pos, embedder.similarity(occ.name, pos), "semantic",
        )
        for occ in rows
    ]


async def search_authoritative(
    position_name: str,
    db: AsyncSession,
    limit: int = 10,
    *,
    neo4j=None,
    embedder=None,
) -> list[dict]:
    """在权威岗位库（occupations 三源：O*NET / 人社部大典 / LinkedIn）检索候选岗位。

    检索方法：pgvector 语义 top-k（occupations.embedding 余弦）+ Neo4j 全文
    关键词 top-k（occupation_search 索引），按 code 合并去重取前 limit 条。
    三源数据均落在同一 occupations 表（source 字段区分），一次检索天然覆盖
    全部来源。降级链：语义路（向量列/扩展/模型任一不可用）→ 跳过；关键词路
    （Neo4j 未同步/不可用）→ PostgreSQL ILIKE。接地是"辅助确认"而非硬门控，
    任一检索源失败不阻塞判定。

    Args:
        position_name: candidate 岗位名
        db: PostgreSQL 会话（权威库检索）
        limit: 返回条数上限（默认 10，对齐设计文档 7.2.3 的 top-10 口径）
        neo4j: Neo4j 驱动（默认 None → 跳过全文路，仅 ILIKE 关键词路）
        embedder: SkillEmbedder（默认 None → 跳过语义路）

    Returns:
        命中的 occupation dict 列表（code/name/category/definition/score）
    """
    pos = _norm(position_name)
    if not pos:
        return []
    if len(pos) < _MATCH_MIN_ALIAS_LEN:
        return []

    semantic_hits = []
    try:
        semantic_hits = await _semantic_search(db, pos, embedder, limit)
    except Exception:
        # 向量列缺失/扩展不可用/模型不可用 → 语义路降级为关键词路
        pass
    keyword_hits = await _keyword_search(neo4j, db, pos, limit)
    return _merge_hits([*semantic_hits, *keyword_hits], limit)


_DEFINITION_SYSTEM_PROMPT = """你是岗位定义专家。根据岗位名称与参考信息，\
用中文撰写简洁的岗位定义草案（1-3 句话）。参考信息可能是英文 O*NET 定义，\
请翻译并结合岗位名凝练成中文。只输出定义本身，不要前缀如"岗位定义："。"""

_DEFINITION_TASK_TEMPLATE = """岗位名称：{position_name}

参考信息（英文权威定义，请翻译并凝练）：
{reference}

输出中文岗位定义草案："""


class _DefinitionDraft(BaseModel):
    """LLM 定义草案输出约束（幻觉防控第一道防线：JSON Schema 强校验）。"""

    text: str


async def _generate_definition(
    position_name: str,
    seed: Optional[dict],
    occupation: Optional[dict],
    llm,
) -> str:
    """生成岗位定义草案。

    优先级：LLM 聚合生成（可配置）→ 权威库定义原文 → 种子描述。
    LLM 失败静默回退，不阻塞接地判定（RAG 接地是"辅助确认"而非硬门控）。
    """
    reference = ""
    if occupation and occupation.get("definition"):
        reference = occupation["definition"]
    elif seed and seed.get("description"):
        reference = seed["description"]

    if not reference:
        return ""

    if llm is not None:
        try:
            # LLM 参与定义草案生成：英文 O*NET 定义 → 中文凝练
            draft = await asyncio.to_thread(
                llm.extract_structured,
                _DEFINITION_TASK_TEMPLATE.format(
                    position_name=position_name, reference=reference
                ),
                _DefinitionDraft,
                system_prompt=_DEFINITION_SYSTEM_PROMPT,
            )
            if draft.text and draft.text.strip():
                return draft.text.strip()
        except Exception:
            # LLM 失败静默回退到原文，不阻塞接地判定
            pass

    # 回退：权威库定义原文（英文）或种子描述
    return reference


async def ground_with_rag(
    position_name: str,
    db: AsyncSession,
    *,
    llm=None,
    seeds_path: Path | None = None,
    neo4j=None,
    embedder=None,
) -> RagGroundingResult:
    """RAG 接地主流程（阶段二）。

    Args:
        position_name: candidate 岗位名
        db: PostgreSQL 会话（权威库检索）
        llm: LLMProviderChain（可选，用于定义草案 LLM 生成）
        seeds_path: 种子 yaml 路径（测试可注入）
        neo4j: Neo4j 驱动（缺省用全局 driver；不可用自动降级 ILIKE 关键词路）
        embedder: SkillEmbedder（缺省用全局单例；不可用自动降级关键词路）

    Returns:
        RagGroundingResult：命中状态 + 定义草案
    """
    seeds = _load_seeds(seeds_path or _SEEDS_PATH)
    seed = match_seed(position_name, seeds)

    # 缺省接入真实双路资源；驱动/模型不可用时由 search_authoritative 内部降级
    if neo4j is None:
        from app.core.database import neo4j_driver

        neo4j = neo4j_driver
    if embedder is None:
        from app.services.matching.semantic import SkillEmbedder

        embedder = SkillEmbedder.get()

    hits = await search_authoritative(position_name, db, neo4j=neo4j, embedder=embedder)
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
