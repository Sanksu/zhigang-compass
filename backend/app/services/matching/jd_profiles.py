"""JD 级岗位画像加载器（阶段 C：把匹配候选从「聚合岗位」切换到「原生 JD」）。

聚合岗位画像（PositionProfile，来自图谱 REQUIRES 聚合边）把几十条 JD 压成一个
「岗位族平均画像」；本模块把每条 JD 的抽取快照直接建成 PositionProfile——
单 JD 的 must（skills）/ nice（requirements）/ 年限 / 行业 / 项目场景，字段与
PositionProfile 完全同构，**`score_position` 无需改动即可评分单 JD**。

候选规模：jd_raw 全量（226 实测 9,912 条）。预筛责任在调用方（阶段 C 设计：
技能命中 ROUGH_SELECT 风格粗选 → 候选 K 保持可控），本模块只做
「行 → PositionProfile」的纯映射与轻量加载。

计分对齐：skill_id = 原始技能名（与候选人侧 skill_name 同口径，engine 用
_canonical_name 对齐）。soft 技能（category 打标软技能）进 soft_requirements
独立通道（与聚合画像一致，2026-08-22 拍板）。
"""

import logging
from typing import Optional

from app.models.raw import JDRaw
from app.services.matching.schemas import (
    Necessity,
    PositionProfile,
    SkillRequirement,
)

logger = logging.getLogger(__name__)

_SOFT_SKILL_CATEGORY = "soft-skills"


def jd_profile_from_snapshot(
    snapshot: dict,
    jd_id: str,
    *,
    source: str = "",
    source_url: str = "",
) -> Optional[PositionProfile]:
    """从 JD 抽取快照构建 PositionProfile（单 JD 画像）。

    快照取 extraction：skills → must（要领），requirements → nice（加分），
    required_years/industry/typical_scenarios/soft_skills 顶层字段透传。
    name 用 JD 标题（若抽取 position_name 缺失），position_id 用 jd_id。
    无抽取/无技能则返回 None（不参与候选）。
    """
    extraction = (snapshot or {}).get("extraction") or {}
    if not isinstance(extraction, dict):
        return None

    skills = extraction.get("skills") or []
    requirements = extraction.get("requirements") or []
    if not skills and not requirements:
        return None

    musts: list[SkillRequirement] = []
    nices: list[SkillRequirement] = []
    softs: list[SkillRequirement] = []

    def _push(spec: dict, *, is_nice: bool, is_soft: bool) -> None:
        name = str(spec.get("name") or spec.get("skill_name") or "").strip()
        if not name:
            return
        req = SkillRequirement(
            skill_id=name, skill_name=name,
            necessity=Necessity.NICE if (is_soft or is_nice) else Necessity.MUST,
            weight=0.5 if (is_soft or is_nice) else 1.0,
            proficiency=spec.get("level"),
        )
        if is_soft:
            softs.append(req)
        elif req.necessity == Necessity.MUST:
            musts.append(req)
        else:
            nices.append(req)

    for s in skills:
        cat = str(s.get("category") or "") if isinstance(s, dict) else ""
        _push(s if isinstance(s, dict) else {"name": s}, is_nice=False, is_soft=cat == _SOFT_SKILL_CATEGORY)
    for r in requirements:
        _push(r if isinstance(r, dict) else {"name": r}, is_nice=True, is_soft=False)

    title = str((snapshot or {}).get("title") or "").strip() or jd_id
    return PositionProfile(
        position_id=jd_id,
        name=title,
        must_skills=musts,
        nice_skills=nices,
        required_years=extraction.get("required_years"),
        industry=extraction.get("industry") or None,
        typical_scenarios=[str(s) for s in (extraction.get("typical_scenarios") or []) if s],
        soft_requirements=softs,
        last_updated=str((snapshot or {}).get("crawled_at") or ""),
    )


def rows_to_profiles(rows: list[JDRaw]) -> tuple[list[PositionProfile], dict[str, str]]:
    """jd_raw 行 → (PositionProfile 列表, jd_id→岗位名映射)。

    岗位名用 snapshot 的 normalized_position（与图谱 Position.name 对齐语义），
    供阶段 C 聚合层按岗位分组；无岗位名（norm 空）的 JD 映射为空串，
    聚合层归入「无归属」低排组。
    """
    profiles: list[PositionProfile] = []
    jd_position: dict[str, str] = {}
    for row in rows:
        snap = row.snapshot or {}
        prof = jd_profile_from_snapshot(
            snap, str(row.id), source=row.source or "", source_url=row.source_url or "",
        )
        jd_position[str(row.id)] = str(
            (snap.get("normalized_position") or "") or
            ((snap.get("extraction") or {}).get("position_name") or "")
        ).strip()
        if prof is not None:
            profiles.append(prof)
    return profiles, jd_position


def rough_select(
    jd_profiles: list[PositionProfile],
    candidate_skill_names: list[str],
    k: int,
) -> list[PositionProfile]:
    """技能命中粗选（阶段 C 预筛：9,912 → k 候选）。

    与 engine.RuleBasedMatcher._rough_select 同口径：候选人技能名与 JD
    must/nice 技能名求交集个数，按命中数降序取 Top-k（0 命中仍保留少量
    兜底，避免纯新技能候选人无候选——聚合画像时代有频次/通配兜底）。
    """
    cand = {s.strip().lower() for s in candidate_skill_names if s and s.strip()}
    if not cand:
        return jd_profiles[:k]

    def hit_count(p: PositionProfile) -> int:
        names = {r.skill_name.strip().lower() for r in (*p.must_skills, *p.nice_skills) if r.skill_name}
        return len(cand & names)

    scored = sorted(jd_profiles, key=hit_count, reverse=True)
    # 命中 ≥1 的优先；只有全部 0 命中（纯新技能冷启动）才启用兜底
    # （保留少量低命中 JD 避免空候选），hits 非空时不混入 0 命中。
    hits = [p for p in scored if hit_count(p) > 0]
    misses = [p for p in scored if hit_count(p) == 0]
    if hits:
        return hits[:k]
    # 全 0 命中：候选全保留（本就是冷启动低命中场景，量小无成本顾虑）
    return misses[:k]