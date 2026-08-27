"""JD 级岗位匹配服务（方案 A：匹配主链路统一到单条原生 JD，彻底移除聚合评分）。

背景（2026-08-27）：recommend（JD 候选模式，最佳单条 JD）与 compare（图谱聚合
画像，数十条 JD 技能并集）口径分裂——同一候选人 × 同一岗位一个 100 分一个 28 分、
差距 227 项。评分公式 `engine.score_position`、权重、sim_threshold、CII/时效/
NoMust 常量一律不动，只改「喂给评分器的岗位画像从哪来」：从 jd_raw 构建**单条
JD** 的 PositionProfile，两条路径共用本模块。

- score_jd_auto：全量 JD → 向量预筛 → 组内多样性配额 → AUTO 评分 → 岗位级聚合
  （recommend 异步任务用，等价旧 workers._match_jd_candidates）。
- score_jd_compare：某岗位名下全部 JD → COMPARE 评分 → 取真正最高分一条
  （compare 同步入口用，详情与推荐同口径单 JD）。

硬边界：本模块不触碰 engine.score_position 的评分逻辑（feeding 来源变更，而非
评分算法变更）。软技能仍走 soft_requirements 独立通道（2026-08-22 拍板）。
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import runtime_config
from app.models.raw import JDRaw
from app.services.matching.engine import RuleBasedMatcher, score_position
from app.services.matching.jd_aggregate import aggregate_jd_scores
from app.services.matching.jd_profiles import (
    diversify_by_position,
    jd_profile_from_snapshot,
    rough_select,
    rows_to_profiles,
)
from app.services.matching.jd_vector_recall import (
    candidate_vector,
    load_pool_vectors_cached,
    vector_recall,
)
from app.services.matching.schemas import (
    CandidateProfile,
    MatchMode,
    MatchRequest,
    MatchResult,
    PositionProfile,
)
from app.services.matching.semantic import SkillEmbedder

logger = logging.getLogger(__name__)

# 08-27：窄 JD 防虚高（对齐口径配套）——JD 须至少 must+nice ≥ 该数才参与匹配/评分。
# 单技能窄 JD（如"只需 Apache Kafka"）命中即满分会系统性抬高 Top-N/详情分数并抹平
# 岗位区分度（实测单技能 JD 使大量岗位冲到 1.0）。算法口径，默认 2，可经
# runtime_config `match_jd_min_skills` 覆盖，需算法岗确认。
_DEFAULT_MIN_JD_SKILLS = 2


def _read_min_jd_skills() -> int:
    """读取窄 JD 过滤阈值（runtime_config 可调，≥1）。"""
    return max(1, int(runtime_config.get("match_jd_min_skills", _DEFAULT_MIN_JD_SKILLS)))


def _jd_width(profile: PositionProfile) -> int:
    """JD 技术技能宽度 = must + nice 技能数（软技能独立通道不计）。"""
    return len(profile.must_skills) + len(profile.nice_skills)


def _run_in_thread(fn):
    """CPU 密集段（SBERT/评分）放线程池，避免阻塞事件循环。"""
    return asyncio.to_thread(fn)


async def load_all_jd_profiles(
    session: AsyncSession,
) -> tuple[list[PositionProfile], dict[str, str]]:
    """jd_raw 全量 → (PositionProfile 列表, jd_id→岗位名映射)。

    行序稳定（按 id）：池化向量指纹顺序无关化的双保险（jd_vector_recall 依赖）。
    jd_position 映射供聚合层按岗位名分组（rows_to_profiles 产出），与 recommend
    旧 `_match_jd_candidates` 口径一致。
    """
    rows = (await session.scalars(
        select(JDRaw)
        .where(JDRaw.snapshot["extraction"].astext.is_not(None))
        .order_by(JDRaw.id)
    )).all()
    return rows_to_profiles(rows)


async def score_jd_auto(
    session: AsyncSession,
    candidate: CandidateProfile,
    project_vectors: dict,
    top_n: int,
    rough_k: int | None = None,
    semantic=None,
) -> list[dict]:
    """recommend 路径：全量 JD → 向量预筛 → 逐条评分 → 岗位级聚合。

    候选源 = jd_raw 抽取快照（非聚合岗位画像）。召回优先 JD 技能池化向量余弦
    Top-K（Redis 缓存池化向量，毫秒级——修复全量评分 36s 性能墙）；SBERT
    不可用降级 rough_select 技能命中粗选。按 snapshot.normalized_position 聚合回
    岗位级展示（Top-N 岗位 + 组内最佳 JD 证据），输出与聚合岗位模式同构。
    """
    rough_k = int(
        rough_k if rough_k is not None else runtime_config.get("match_jd_rough_k", 50)
    )
    embedder = semantic or SkillEmbedder.get()

    jd_profiles, jd_position = await load_all_jd_profiles(session)
    if not jd_profiles:
        return []
    # 08-27 窄 JD 防虚高：单技能窄 JD 命中即满分会系统性抬高分数、抹平岗位区分度
    # ——recommend 与 compare 同口径先按最小技能宽度过滤（阈值 runtime_config 可调）
    min_skills = _read_min_jd_skills()
    jd_profiles = [p for p in jd_profiles if _jd_width(p) >= min_skills]
    if not jd_profiles:
        return []

    candidate_skills = [s.skill_name for s in candidate.skills if s.skill_name]

    # 向量预筛：Redis 读写留主循环，CPU 段内部 to_thread；SBERT 不可用 → None 降级
    from app.core.database import redis_client

    try:
        pool_vecs = await load_pool_vectors_cached(jd_profiles, embedder, redis_client)
    except Exception as e:
        logger.warning("[match/jd_mode] 池化向量加载失败（降级命中粗选）: %s", e)
        pool_vecs = None

    pool = None
    if pool_vecs:
        def _recall():
            cand_vec = candidate_vector(candidate_skills, embedder)
            if cand_vec is None:
                return None
            return vector_recall(jd_profiles, pool_vecs, cand_vec, k=rough_k)

        pool = await _run_in_thread(_recall)
    if pool is None:
        pool = rough_select(jd_profiles, candidate_skills, k=rough_k)
    if not pool:
        return []

    # 岗位多样性配额（第六轮审查算法口径 1）：防同族 JD 占满召回席位
    pool = diversify_by_position(pool, jd_position, rough_k, top_n)

    def _score():
        matcher = RuleBasedMatcher(pool, semantic=embedder)
        return matcher.match(
            MatchRequest(
                candidate=candidate,
                mode=MatchMode.AUTO,
                top_n=min(len(pool), 100),
                project_vectors=project_vectors,
            )
        )

    scored = await _run_in_thread(_score)
    results = aggregate_jd_scores(scored, jd_position, top_n=top_n)
    # 08-27 fix：Top-N 与 compare 对齐——召回池(rough_k=50 全量粗选)可能漏掉
    # 某岗位的最佳 JD，导致列表分(池内最高分)≠详情分(compare 用该岗位全部 JD
    # 取真最高分)。对最终 Top-N 岗位补全量评分取真最高分，覆盖聚合分数与证据。
    if results:
        results = await _align_scores_with_full_jd(
            session, candidate, results, project_vectors, embedder,
        )
    return results


async def _align_scores_with_full_jd(
    session: AsyncSession,
    candidate: CandidateProfile,
    results: list[dict],
    project_vectors: dict,
    semantic=None,
) -> list[dict]:
    """08-27 fix：Top-N 岗位分与 compare 对齐——取该岗位名下全部 JD 的真最高分。

    recommend 召回池（rough_k 全量粗选 Top-K）可能漏掉某岗位的最佳 JD，导致
    列表分 ≠ 详情分（compare 加载该岗位全部 JD 评分取真最高分，见 score_jd_compare）。
    对每个 Top-N 岗位复用 score_jd_compare 取真最高分，覆盖聚合分数与证据
    （真最高分 JD 置顶）。score 只会 ≥ 池内 max（全量集是池内集超集），仍防御性
    仅在高分时覆盖。量级 = top_n 岗位 × 每岗位 JD 数，与 compare 单岗位同构，
    池内少量重复评分可忽略（recommend 为异步任务，不阻塞同步响应）。
    """
    for item in results:
        found = await score_jd_compare(
            session, candidate, item["position_id"], project_vectors, semantic=semantic,
        )
        if found is None:
            continue
        _, best_result = found
        if best_result.total_score >= item["total_score"]:
            item.update({
                "total_score": round(best_result.total_score, 4),
                "must_score": best_result.must_score,
                "nice_score": round(best_result.nice_score, 4),
                "exp_score": round(best_result.exp_score, 4),
                "matched_must": best_result.matched_must,
                "missing_must": best_result.missing_must,
                "summary": best_result.summary,
                "unqualified": best_result.unqualified,
            })
        ev = [e for e in item["jd_evidence"] if e["jd_id"] != best_result.position_id]
        item["jd_evidence"] = [
            {
                "jd_id": best_result.position_id,
                "jd_title": best_result.position_name,
                "total_score": round(best_result.total_score, 4),
                "hit_count": len(best_result.matched_must) + len(best_result.matched_nice),
            },
            *ev,
        ]
    results.sort(key=lambda r: r["total_score"], reverse=True)
    return results


async def score_jd_compare(
    session: AsyncSession,
    candidate: CandidateProfile,
    position_name: str,
    project_vectors: dict,
    semantic=None,
    sim_threshold: float | None = None,
) -> tuple[PositionProfile, MatchResult] | None:
    """compare 路径：该岗位名下全部 JD → COMPARE 评分 → 取真正最高分一条。

    position_name：岗位名（recommend 列表 position_id=岗位名，normalized_position
    口径，与 jd_aggregate 分组 key 一致）。加载该岗位名下全量 JD（不限 50——
    详情要取真最佳），全部评分后取 max(members, key=total_score) 作为详情基准，
    保证列表分数与详情同口径单 JD。

    返回 (最佳 PositionProfile, 最佳 MatchResult)；无该岗位 JD 返回 None。
    """
    embedder = semantic or SkillEmbedder.get()
    rows = (await session.scalars(
        select(JDRaw)
        .where(JDRaw.snapshot["normalized_position"].astext == position_name)
        .order_by(JDRaw.id)
    )).all()

    min_skills = _read_min_jd_skills()
    profiles: list[PositionProfile] = []
    for row in rows:
        if (row.snapshot or {}).get("extraction") is None:
            continue
        prof = jd_profile_from_snapshot(
            row.snapshot or {}, str(row.id),
            source=row.source or "", source_url=row.source_url or "",
        )
        # 08-27 窄 JD 防虚高：与 recommend 同口径过滤单技能窄 JD（避免命中即满分）
        if prof is not None and _jd_width(prof) >= min_skills:
            profiles.append(prof)
    if not profiles:
        return None

    def _score():
        best_profile: PositionProfile | None = None
        best: MatchResult | None = None
        for prof in profiles:
            result = score_position(
                candidate, prof, semantic=embedder, sim_threshold=sim_threshold,
                project_vectors=project_vectors,
            )
            if best is None or result.total_score > best.total_score:
                best, best_profile = result, prof
        return best_profile, best

    return await _run_in_thread(_score)


async def load_jd_evidence_refs(
    session: AsyncSession,
    position_name: str,
    max_skills: int = 20,
) -> list[dict]:
    """岗位名下 JD → 技能证据引用（skill → JD 采集源）。

    替代旧 Neo4j (`Position:REQUIRES→Skill→EVIDENCED_BY→Evidence`) 链路——方案 A
    后岗位画像来自单条 JD，证据自然来自该岗位名下 jd_raw 行：每条技能记录其被
    哪些 JD 要求（来源/source_url），供前端「技能断言可追溯至原始 JD」展示。
    每技能至多 1 条代表证据（同源去重），总上限 max_skills。
    """
    rows = (await session.scalars(
        select(JDRaw)
        .where(JDRaw.snapshot["normalized_position"].astext == position_name)
        .order_by(JDRaw.id)
    )).all()

    skill_sources: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        extraction = (row.snapshot or {}).get("extraction") or {}
        if not isinstance(extraction, dict):
            continue
        skills = extraction.get("skills") or []
        names = [
            str(s.get("name")) for s in skills if isinstance(s, dict) and s.get("name")
        ]
        names += [str(s) for s in skills if isinstance(s, str) and s]
        src = row.source or ""
        url = row.source_url or ""
        seen_in_row: set[str] = set()
        for name in names:
            if not name or name in seen_in_row:
                continue
            seen_in_row.add(name)
            skill_sources.setdefault(name, []).append((src, url))

    out: list[dict] = []
    for skill, specs in skill_sources.items():
        if len(out) >= max_skills:
            break
        # 同源去重（每技能至多 1 条代表证据）
        seen_src: set[str] = set()
        for src, url in specs:
            if src in seen_src:
                continue
            seen_src.add(src)
            out.append({"skill": skill, "source": src, "url": url})
            break
    return out
