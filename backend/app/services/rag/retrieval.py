"""通用 RAG 检索模块（设计文档 §6.4 RAG 检索增强）。

供诊断报告等生成链路动态检索图谱上下文，检索源：
- 图谱岗位定义（discovery_candidates 中 candidate/emerging/stable 的 definition_draft
  + occupations 三源权威定义：O*NET / 人社部大典 / LinkedIn）
- 技能描述（Neo4j Skill 节点全文，skill_search 索引）
- 历史诊断报告（diagnosis_reports 表）

检索方法（§6.4 原文）：Neo4j 全文索引（关键词）+ pgvector 向量检索（语义）合并去重；
上下文窗口截取前 max_tokens（默认 3000 tokens），超出丢弃。
每条 chunk 附 evidence_id（position:/occupation:/skill:/diagnosis: 前缀）供生成约束引用，
实现"仅基于证据生成回答 + 虚构引用拦截"（§6.4 生成约束）。

任一检索源不可用自动跳过（RAG 是增强，不阻塞诊断生成）。
"""

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import DiagnosisReportRecord, DiscoveryCandidate

# 图谱岗位定义检索状态。candidate 定义草案一并纳入（RAG 是增强，多一份上下文比
# 空上下文好）；candidate 定义质量低于人工确认的 emerging/stable，靠 score 排序兜底。
_VERIFIED_STATES = ("candidate", "emerging", "stable")

DEFAULT_TOP_K = 10
DEFAULT_MAX_TOKENS = 3000

# Neo4j 全文查询（Lucene 语法）特殊字符：查询前剔除，避免语法异常
_LUCENE_SPECIAL = frozenset('+-&|!(){}[]^"~*?:\\/')


def _sanitize_fulltext(q: str) -> str:
    """剔除 Neo4j 全文查询的 Lucene 特殊字符，空串视为无关键词命中。"""
    return "".join(ch for ch in q if ch not in _LUCENE_SPECIAL).strip()


def _estimate_tokens(text: str) -> int:
    """token 估算：中文 1 字 ≈ 1 token，其余字符 4 字符 ≈ 1 token（§6.4 3000 上限用）。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + (other + 3) // 4


@dataclass
class RetrievedChunk:
    """单条检索命中，供生成链路渲染为上下文并附 evidence_id 引用。"""

    content: str
    evidence_id: str
    source: str  # position_definition | occupation | skill | diagnosis
    score: float = 0.5


async def _verified_positions(db: AsyncSession, query: str) -> list[RetrievedChunk]:
    """图谱岗位定义（discovery_candidates：candidate/emerging/stable 且定义非空）。

    关键词路：岗位名 ILIKE 子串匹配（图谱岗位为人工审核确认的中文名）。
    """
    q = (query or "").strip().lower()
    if not q:
        return []
    rows = (
        await db.scalars(
            select(DiscoveryCandidate)
            .where(
                DiscoveryCandidate.state.in_(_VERIFIED_STATES),
                DiscoveryCandidate.position_name.ilike(f"%{q}%"),
            )
            .order_by(DiscoveryCandidate.position_name)
            .limit(DEFAULT_TOP_K)
        )
    ).all()
    chunks = []
    for r in rows:
        definition = (r.definition_draft or "").strip()
        if not definition:
            continue
        chunks.append(
            RetrievedChunk(
                content=f"{r.position_name}：{definition}",
                evidence_id=f"position:{r.position_name}",
                source="position_definition",
                score=1.0,
            )
        )
    return chunks


async def _occupations(
    db: AsyncSession, query: str, *, embedder=None, neo4j=None
) -> list[RetrievedChunk]:
    """occupations 三源权威定义（复用 discovery.grounding 双路检索：语义 + 全文）。"""
    from app.services.discovery.grounding import search_authoritative

    hits = await search_authoritative(query, db, neo4j=neo4j, embedder=embedder)
    chunks = []
    for h in hits:
        definition = (h.get("definition") or "").strip()
        if not definition:
            continue
        chunks.append(
            RetrievedChunk(
                content=f"{h.get('name')}：{definition}",
                evidence_id=f"occupation:{h.get('code')}",
                source="occupation",
                score=float(h.get("score", 0.5)),
            )
        )
    return chunks


async def _skills(neo4j, query: str) -> list[RetrievedChunk]:
    """技能描述（Neo4j skill_search 全文索引关键词路）。"""
    q = _sanitize_fulltext(query)
    if neo4j is None or not q:
        return []
    try:
        with neo4j.session() as session:
            rows = session.run(
                "CALL db.index.fulltext.queryNodes('skill_search', $q) "
                "YIELD node, score "
                "RETURN node.name AS name, node.description AS description, score "
                "LIMIT $limit",
                q=q,
                limit=DEFAULT_TOP_K,
            ).data()
    except Exception:
        # Neo4j 不可达：跳过技能源，不阻塞检索
        return []
    chunks = []
    for r in rows:
        name = r.get("name") or ""
        if not name:
            continue
        desc = (r.get("description") or "").strip()
        content = f"{name}：{desc}" if desc else name
        chunks.append(
            RetrievedChunk(
                content=content,
                evidence_id=f"skill:{name}",
                source="skill",
                score=float(r.get("score", 0.5)),
            )
        )
    return chunks


async def _diagnoses(db: AsyncSession, query: str) -> list[RetrievedChunk]:
    """历史诊断报告（diagnosis_reports 按岗位名匹配，取总体结论与 Top-3 差距建议）。"""
    q = (query or "").strip().lower()
    if not q:
        return []
    rows = (
        await db.scalars(
            select(DiagnosisReportRecord)
            .where(DiagnosisReportRecord.position_name.ilike(f"%{q}%"))
            .order_by(DiagnosisReportRecord.created_at.desc())
            .limit(DEFAULT_TOP_K)
        )
    ).all()
    chunks = []
    for r in rows:
        report = r.report or {}
        summary = (report.get("overall_summary") or "").strip()
        if not summary:
            continue
        gaps = "；".join(
            f"{g.get('skill', '')}:{g.get('advice', '')}"
            for g in (report.get("top_gaps") or [])[:3]
        )
        content = f"岗位 {r.position_name} 历史诊断：{summary}"
        if gaps:
            content += f"；差距建议：{gaps}"
        chunks.append(
            RetrievedChunk(
                content=content,
                evidence_id=f"diagnosis:{r.match_id}",
                source="diagnosis",
                score=1.0,
            )
        )
    return chunks


def allowed_evidence_ids(
    rag_chunks: list[dict], evidence_refs: Optional[list[dict]] = None
) -> set[str]:
    """虚构引用拦截的允许集合（§6.4 生成约束）。

    合法引用来源：
    - RAG 上下文命中携带的 evidence_id（position:/occupation:/skill:/diagnosis:）
    - 匹配快照证据（evidence_refs）的真实来源 source / url

    generate_diagnosis 据此校验 LLM 生成的断言引用：不在集合内 → 虚构引用置空。
    """
    allowed = {c.get("evidence_id", "") for c in rag_chunks}
    for e in evidence_refs or []:
        if e.get("source"):
            allowed.add(e["source"])
        if e.get("url"):
            allowed.add(e["url"])
    return {x for x in allowed if x}


async def retrieve_context(
    query: str,
    db: AsyncSession,
    *,
    neo4j=None,
    embedder=None,
    top_k: int = DEFAULT_TOP_K,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[RetrievedChunk]:
    """检索图谱上下文（岗位定义/技能/历史诊断报告），合并去重后按 token 上限截断。

    Args:
        query: 检索关键词（诊断场景为岗位名）
        db: PostgreSQL 会话
        neo4j: Neo4j 驱动（默认 None → 跳过全文路；诊断端点注入全局 driver）
        embedder: SkillEmbedder（默认 None → 权威库语义路跳过，仅关键词路）
        top_k: 单检索源返回条数上限（默认 10，对齐 §6.4"各检索路 top-k"）
        max_tokens: 上下文窗口 token 上限（默认 3000，§6.4 原文，超出丢弃）

    Returns:
        每源 top_k 合并、按 score 降序、evidence_id 去重、token 截断后的
        RetrievedChunk 列表。任一检索源失败自动跳过（RAG 是增强，不阻塞生成链路）。
    """
    query = (query or "").strip()
    if not query:
        return []

    results: list[RetrievedChunk] = []
    results += await _verified_positions(db, query)
    results += await _occupations(db, query, embedder=embedder, neo4j=neo4j)
    results += await _skills(neo4j, query)
    results += await _diagnoses(db, query)

    # 各源各自按 score 取 top_k（对齐 §6.4"各检索路 top-k 合并去重"）。
    # 不跨源混排：skill 全文分数（5-8）与 occupation 余弦（0-1）/position 固定分
    # 量纲不同，混排会让高分源垄断上下文。
    per_source: dict[str, list[RetrievedChunk]] = {}
    for c in results:
        per_source.setdefault(c.source, []).append(c)
    pooled: list[RetrievedChunk] = []
    for chunks in per_source.values():
        chunks.sort(key=lambda c: c.score, reverse=True)
        pooled.extend(chunks[:top_k])

    # 按 evidence_id 合并去重（保留高分），score 降序
    best: dict[str, RetrievedChunk] = {}
    for c in pooled:
        cur = best.get(c.evidence_id)
        if cur is None or c.score > cur.score:
            best[c.evidence_id] = c
    ranked = sorted(best.values(), key=lambda c: c.score, reverse=True)

    # 上下文窗口：截取前 max_tokens（§6.4），超出丢弃
    used = 0
    kept: list[RetrievedChunk] = []
    for c in ranked:
        cost = _estimate_tokens(c.content)
        if used + cost > max_tokens:
            continue
        used += cost
        kept.append(c)
    return kept
