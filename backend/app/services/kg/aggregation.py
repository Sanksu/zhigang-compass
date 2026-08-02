"""岗位聚合（设计文档 §4.5 数据交叉验证 / §5.5 REQUIRES 边 weight 聚合）。

输入 jd_raw 已抽取记录（snapshot.extraction），计算岗位热度与技能边权重，
全量重算写回 Neo4j（幂等：重复执行仅覆盖既有值，不产生重复节点）。

聚合口径：
- Position.freq           = 命中该岗位名的 JD 条数（Evidence 数）
- Position.required_years = 该岗位 JD 经验要求最小年限的中位数（无则保留原值）
- Position.last_updated   = 本次聚合时间
- REQUIRES.weight         = must=0.8 / nice=0.4（沿用图谱现有两档约定）
- REQUIRES.necessity      = 该技能在岗位 JD 中 must 占比 ≥ 0.5 判 must，否则 nice
- REQUIRES.source_count   = 命中该技能的独立招聘源数

weight 取离散两档而非出现率连续值的原因：与匹配引擎 CII 降级（按 weight
升序降级边缘必备项）和全景图 min_weight=0.3 过滤语义兼容，且与历史手工
聚合数据格式一致。
"""

from __future__ import annotations

import re
from collections import defaultdict
from statistics import median

# 图谱 weight 两档约定
_WEIGHT_MUST = 0.8
_WEIGHT_NICE = 0.4
# 技能判 must 的 must 出现占比阈值
_MUST_MAJORITY = 0.5


class SkillAgg:
    __slots__ = ("hit", "must_count", "sources")

    def __init__(self) -> None:
        self.hit = 0
        self.must_count = 0
        self.sources: set[str] = set()


class PositionAgg:
    __slots__ = ("jd_count", "skills", "exp_years")

    def __init__(self) -> None:
        self.jd_count = 0
        self.skills: dict[str, SkillAgg] = defaultdict(SkillAgg)
        self.exp_years: list[float] = []


def _min_experience_years(snapshot: dict) -> float | None:
    """解析 JD 经验要求最小年限（如 "3-5年" → 3.0），无法解析返回 None。"""
    m = re.search(r"(\d+)", str(snapshot.get("experience") or ""))
    return float(m.group(1)) if m else None


def _position_skills(ext: dict) -> list[tuple[str, str]]:
    """岗位技能列表 (skill_name, necessity)。requirements 优先，缺省 skills。"""
    reqs = ext.get("requirements") or []
    if reqs:
        return [
            (r.get("skill_name", "").strip(), r.get("necessity", "nice"))
            for r in reqs
            if r.get("skill_name") and r["skill_name"].strip()
        ]
    return [
        (s.get("name", "").strip(), "nice")
        for s in (ext.get("skills") or [])
        if s.get("name") and s["name"].strip()
    ]


def build_aggregates(rows) -> dict[str, PositionAgg]:
    """从 jd_raw 已抽取记录聚合。

    rows 需为 JDRaw ORM 行（使用 row.snapshot / row.source）。
    空岗位名（正文质量差导致的空抽取）不参与聚合。
    """
    agg: dict[str, PositionAgg] = defaultdict(PositionAgg)
    for row in rows:
        snap = row.snapshot or {}
        ext = snap.get("extraction") or {}
        pos = (ext.get("position_name") or "").strip()
        if not pos:
            continue
        pa = agg[pos]
        pa.jd_count += 1
        years = _min_experience_years(snap)
        if years is not None:
            pa.exp_years.append(years)
        source = row.source or ""
        for skill, necessity in _position_skills(ext):
            sa = pa.skills[skill]
            sa.hit += 1
            sa.sources.add(source)
            if necessity == "must":
                sa.must_count += 1
    return agg


def write_aggregates(session, agg: dict[str, PositionAgg], now: str) -> dict:
    """全量写回 Neo4j（UNWIND 批量 + MERGE 幂等）。返回写入的岗位/边数。"""
    positions = []
    edges = []
    for pos, pa in agg.items():
        positions.append({
            "pos": pos,
            "freq": pa.jd_count,
            "req_years": median(pa.exp_years) if pa.exp_years else None,
            "now": now,
        })
        for skill, sa in pa.skills.items():
            is_must = sa.must_count / sa.hit >= _MUST_MAJORITY
            edges.append({
                "pos": pos,
                "skill": skill,
                "weight": _WEIGHT_MUST if is_must else _WEIGHT_NICE,
                "necessity": "must" if is_must else "nice",
                "source_count": len(sa.sources),
            })

    with session:
        if positions:
            session.run(
                """
                UNWIND $items AS it
                MATCH (p:Position {name: it.pos})
                SET p.freq = it.freq,
                    p.last_updated = it.now,
                    p.required_years = coalesce(it.req_years, p.required_years)
                """,
                items=positions,
            )
        if edges:
            session.run(
                """
                UNWIND $edges AS e
                MATCH (p:Position {name: e.pos}), (s:Skill {name: e.skill})
                MERGE (p)-[r:REQUIRES]->(s)
                SET r.weight = e.weight,
                    r.necessity = e.necessity,
                    r.source_count = e.source_count
                """,
                edges=edges,
            )
    return {"positions": len(positions), "edges": len(edges)}
