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
from app.services.matching.jd_aggregate import (
    _AGGREGATE_TOP_JD_EVIDENCE,
    aggregate_jd_scores,
)
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


# 08-27：对齐全量重评的成本上界——每岗位参与评分的 JD 数上限（按 updated_at 最近 N 条宽 JD）。
# 不设上限会对岗位全量 JD 评分（后端开发工程师 850 条 → recommend 对齐 133s 超时）。
# compare 与对齐同用 score_jd_compare（同口径同上限），列表/详情分数仍一致。
# 算法口径，默认 50，可 runtime_config `match_jd_max_per_position` 覆盖。
_DEFAULT_MAX_JDS_PER_POSITION = 50


def _read_max_jds_per_position() -> int:
    """读取每岗位评分 JD 数上限（runtime_config 可调，≥1）。"""
    return max(1, int(runtime_config.get("match_jd_max_per_position", _DEFAULT_MAX_JDS_PER_POSITION)))


def _jd_width(profile: PositionProfile) -> int:
    """JD 技术技能宽度 = must + nice **唯一技能名数**（软技能独立通道不计）。

    同名技能在 extraction 的 skills[] 与 requirements[] 双列重复出现（抽取常见，
    如 Kafka 同时进 skills 和 requirements）只计一次——按条数计数会把单技能 JD 撑成
    2 而漏过窄 JD 过滤，导致命中即满分虚高（08-27 E2E 实测）。
    """
    return len({r.skill_name for r in (*profile.must_skills, *profile.nice_skills)})


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
    """08-27 fix：Top-N 岗位分与 compare 对齐——取该岗位最近 N 条宽 JD 的真最高分。

    recommend 召回池（rough_k 全量粗选 Top-K）与 compare 的候选集不同，同一岗位
    可能取到不同 JD 导致列表分 ≠ 详情分。对每个 Top-N 岗位复用 score_jd_compare
    （与 compare 同口径：窄 JD 过滤 + 每岗位评分上限）取真最高分，**无条件覆盖**
    聚合分数与证据（真最高分 JD 置顶），保证列表分 === 详情分。量级 = top_n 岗位
    × 每岗位评分上限（默认 50），recommend 为异步任务，不阻塞同步响应。
    """
    for item in results:
        found = await score_jd_compare(
            session, candidate, item["position_id"], project_vectors, semantic=semantic,
        )
        if found is None:
            continue
        _, best_result = found
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
        # 真最高分 JD 置顶后截断 Top-2，与契约 jd_evidence「Top-2」描述对齐
        item["jd_evidence"] = ([
            {
                "jd_id": best_result.position_id,
                "jd_title": best_result.position_name,
                "total_score": round(best_result.total_score, 4),
                "hit_count": len(best_result.matched_must) + len(best_result.matched_nice),
            },
            *ev,
        ])[:_AGGREGATE_TOP_JD_EVIDENCE]
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
    """compare 路径：该岗位名下 JD → COMPARE 评分 → 取最高分一条。

    position_name：岗位名（recommend 列表 position_id=岗位名，normalized_position
    口径，与 jd_aggregate 分组 key 一致）。按 updated_at 最近优先加载该岗位名下
    JD，最多评分 max_jds（默认 50）条宽 JD（与 recommend 对齐同口径同上限，
    385b3f1 修 133s 超时），取 max(members, key=total_score) 作为详情基准，
    保证列表分数与详情同口径单 JD。最高分口径 = 最近 N 条宽 JD 中的最高分，
    非全局全量（SQL 侧同步 limit 兜底，防大岗位全量拉取 snapshot）。

    返回 (最佳 PositionProfile, 最佳 MatchResult)；无该岗位 JD 返回 None。
    """
    embedder = semantic or SkillEmbedder.get()
    min_skills = _read_min_jd_skills()
    max_jds = _read_max_jds_per_position()
    rows = (await session.scalars(
        select(JDRaw)
        .where(JDRaw.snapshot["normalized_position"].astext == position_name)
        .order_by(JDRaw.updated_at.desc(), JDRaw.id)  # 最近优先，配合每岗位评分上限
        # SQL 侧 limit 兜底：评分循环 50 条宽 JD 即 break，但无 limit 会对大岗位
        # （850 条）全量拉取完整 snapshot JSONB×2（compare + evidence_refs 两查）
        .limit(max_jds * 4)
    )).all()
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
            # 每岗位评分上限：只评最近 max_jds 条宽 JD（全量重评导致 recommend
            # 对齐 133s 超时，见 _DEFAULT_MAX_JDS_PER_POSITION 注释）
            if len(profiles) >= max_jds:
                break
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
