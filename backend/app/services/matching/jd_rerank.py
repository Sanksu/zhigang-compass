"""JD 级证据精排（阶段 B：岗位族内原生 JD 二次精排）。

匹配引擎生产路径返回「聚合岗位画像」（REQUIRES 聚合后 Top-N Position），
本模块在命中岗位**族内**对原生 JD 做一次轻量精排：候选人技能集 vs
JD 抽取技能集求覆盖，找出「最匹配的 1-3 条真实 JD」作证据展示。
候选规模天然受限（只查命中岗位名下 JD，不动 141 全局候选集），
是「单 JD 直配」评估里的低风险增量（阶段 B）。

计数语义（与图谱聚合不同，这里保留 JD 原始形态）：
- 覆盖度 = |候选人技能 ∩ JD 技能| / max(1, |JD 技能|)（JD 要求被满足的比例）
- 命中数 = |候选人技能 ∩ JD 技能|（绝对量，直观展示）
- must 优先：JD 抽取的 skills 视为 must（聚合口径同），requirements 视为 nice
  加权（nice 命中 ×0.5 后并入覆盖分子，非阻断）

仅作展示证据，不改动匹配引擎评分/权重/顺序。
"""

import logging

from sqlalchemy import select

logger = logging.getLogger(__name__)

_DEFAULT_JD_EVIDENCE_K = 2  # 每命中岗位展示的 JD 证据条数
_MAX_JD_CANDIDATES = 50  # 该岗位名下最多考察的 JD 数（控制 DB/计算成本）


def _jd_skill_names(extraction: dict) -> tuple[list[str], list[str]]:
    """从抽取快照提取 must（skills）+ nice（requirements）技能名列表。"""
    musts: list[str] = []
    nices: list[str] = []
    for s in (extraction.get("skills") or []):
        if isinstance(s, dict) and s.get("name"):
            musts.append(str(s["name"]))
        elif isinstance(s, str) and s:
            musts.append(s)
    for r in (extraction.get("requirements") or []):
        if isinstance(r, dict) and r.get("skill_name"):
            nices.append(str(r["skill_name"]))
        elif isinstance(r, dict) and r.get("name"):
            nices.append(str(r["name"]))
    return musts, nices


def _coverage_score(candidate_skills: set[str], musts: list[str], nices: list[str]) -> float:
    """JD 要求被满足比例（nice 半计入，非阻断）。"""
    cand = candidate_skills
    must_hit = sum(1 for s in musts if s in cand)
    nice_hit = sum(1 for s in nices if s in cand)
    total = len(musts) + 0.5 * len(nices)
    if total <= 0:
        return 0.0
    return (must_hit + 0.5 * nice_hit) / total


def rank_jds_for_position(
    rows: list,
    position_name: str,
    candidate_skills: list[str],
    k: int = _DEFAULT_JD_EVIDENCE_K,
) -> list[dict]:
    """对某岗位名下 JD 行做精排，返回 Top-K 证据。

    rows: jd_raw ORM 行（snapshot 含 extraction/title；source/source_url 随行）。
    position_name: 图谱 Position.name（与 JD normalized_position_from_snapshot 对齐）。
    返回 [{position_name, jd_title, source, source_url, coverage, hit_count,
           must_total, nice_total, hit_skills}]
    """
    cand = {s for s in candidate_skills if s}
    scored: list[dict] = []
    for row in rows:
        snap = row["snapshot"] or {}
        extraction = snap.get("extraction") or {}
        jd_name = str(
            (snap.get("normalized_position") or "")
            or ((extraction or {}).get("position_name") or "")
        ).strip()
        # 行主据岗位名过滤（与图谱 Position.name 对齐语义）
        if jd_name != position_name:
            continue
        musts, nices = _jd_skill_names(extraction)
        cov = _coverage_score(cand, musts, nices)
        if cov <= 0 and not musts:
            continue
        hit_skills = [s for s in (*musts, *nices) if s in cand]
        scored.append({
            "position_name": position_name,
            "jd_title": str(snap.get("title") or "").strip() or "(无标题)",
            "source": row.get("source") or "",
            "source_url": row.get("source_url") or "",
            "coverage": round(cov, 4),
            "hit_count": len(hit_skills),
            "must_total": len(musts),
            "nice_total": len(nices),
            "hit_skills": hit_skills[:8],
        })
    scored.sort(key=lambda x: (-x["coverage"], -x["hit_count"]))
    return scored[:k]


async def load_jd_rows_for_position(
    session,
    position_name: str,
    limit: int = _MAX_JD_CANDIDATES,
) -> list:
    """加载某岗位名下的 JD 行（最近优先，限数控制成本）。

    用 snapshot->normalized_position 初筛（持久化口径），行级再经
    rank_jds_for_position 二次校验（重算兜底）。缺列宽表仅取所需列。
    """
    from app.models.raw import JDRaw

    rows = (await session.scalars(
        select(JDRaw)
        .where(JDRaw.snapshot["normalized_position"].astext == position_name)
        .order_by(JDRaw.updated_at.desc())
        .limit(limit)
    )).all()
    return [
        {"snapshot": r.snapshot or {}, "source": r.source or "",
         "source_url": r.source_url or ""}
        for r in rows
    ]


def enrich_with_jd_evidence(
    results: list,
    jd_rows_by_position: dict,
    candidate_skills: list[str],
    k: int = _DEFAULT_JD_EVIDENCE_K,
) -> None:
    """给 match 结果列表附 JD 级证据（原地改 dict，不动 MatchResult schema）。

    results: match_recommend 的 result.model_dump() 后 dict 列表（含 position_name）。
    jd_rows_by_position: position_name → JD 行列表（load_jd_rows_for_position 输出）。
    """
    for item in results:
        pname = item.get("position_name") or ""
        rows = jd_rows_by_position.get(pname) or []
        if not rows:
            item["jd_evidence"] = []
            continue
        item["jd_evidence"] = rank_jds_for_position(rows, pname, candidate_skills, k=k)