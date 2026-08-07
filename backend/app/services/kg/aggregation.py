"""岗位聚合（设计文档 §4.5 数据交叉验证 / §5.5 REQUIRES 边 weight 聚合）。

输入 jd_raw 已抽取记录（snapshot.extraction），计算岗位热度与技能边权重，
全量重算写回 Neo4j（幂等：重复执行仅覆盖既有值，不产生重复节点）。

聚合口径：
- Position.freq           = 命中该岗位名的 JD 条数（Evidence 数）
- Position.required_years = 该岗位 JD 经验要求最小年限的中位数（无则保留原值）
- Position.last_updated   = 本次聚合时间
- Position.soft_skills    = 软技能白名单（按 JD 命中数降序，设计文档 9.2 节）
- REQUIRES.weight         = must=0.8 / nice=0.4（沿用图谱现有两档约定）
- REQUIRES.necessity      = JD 数 ≥3 时 must 标注覆盖率（must_count/jd_count）> 1/2 判 must；
                            样本不足（<3 条）回退 must 标注占比（must_count/hit）≥ 1/2
- REQUIRES.source_count   = 命中该技能的独立招聘源数

weight 取离散两档而非出现率连续值的原因：与匹配引擎 CII 降级（按 weight
升序降级边缘必备项）和全景图 min_weight=0.3 过滤语义兼容，且与历史手工
聚合数据格式一致。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from statistics import median

from app.services.extraction.dictionary import SOFT_SKILL_WHITELIST

# 图谱 weight 两档约定
_WEIGHT_MUST = 0.8
_WEIGHT_NICE = 0.4
# must 判定阈值：jd_count≥3 时要求 must 标注覆盖率 >1/2；
# 样本不足（<3 条）回退 must 标注占比 ≥1/2（两处阈值一致，分母不同）
_MUST_THRESHOLD = 0.5


def _most_common_level(levels: list[str]) -> str:
    """熟练度众数（并列取出现最早的一档）；无 level 返回空串。"""
    if not levels:
        return ""
    return Counter(levels).most_common(1)[0][0]


def _is_must(sa: SkillAgg, jd_count: int) -> bool:
    """技能边是否判 must（设计文档 §5.5 聚合口径）。

    岗位 JD 数 ≥3 时按 must 标注覆盖率判定（must_count/jd_count > 1/2）：
    技能须在该岗位过半数 JD 中被 LLM 标为 must 才算必须——原"must 标注占比
    过半"口径下每条 JD 各自抽取的 must 技能差异大，导致 85% 边被判 must；
    样本不足（<3 条）回退原逻辑（must 标注占比 ≥1/2），不因样本少武断降级。
    """
    if jd_count >= 3:
        return sa.must_count / jd_count > _MUST_THRESHOLD
    return sa.must_count / sa.hit >= _MUST_THRESHOLD


class SkillAgg:
    __slots__ = ("hit", "must_count", "sources", "levels")

    def __init__(self) -> None:
        self.hit = 0
        self.must_count = 0
        self.sources: set[str] = set()
        # 该技能在岗位 JD 中的熟练度 level 收集（聚合取众数写回 REQUIRES.level）
        self.levels: list[str] = []


class PositionAgg:
    __slots__ = ("jd_count", "skills", "exp_years", "soft_skills")

    def __init__(self) -> None:
        self.jd_count = 0
        self.skills: dict[str, SkillAgg] = defaultdict(SkillAgg)
        self.exp_years: list[float] = []
        # 软技能白名单命中的 JD 数（写回 Position.soft_skills，设计文档 9.2 节）
        self.soft_skills: Counter = Counter()


def _min_experience_years(snapshot: dict) -> float | None:
    """解析 JD 经验要求最小年限（如 "3-5年" → 3.0），无法解析返回 None。"""
    m = re.search(r"(\d+)", str(snapshot.get("experience") or ""))
    return float(m.group(1)) if m else None


def _position_skills(ext: dict) -> list[tuple[str, str, str]]:
    """岗位技能列表 (skill_name, necessity, level)。requirements 优先，缺省 skills。"""
    reqs = ext.get("requirements") or []
    if reqs:
        return [
            (
                r.get("skill_name", "").strip(),
                r.get("necessity", "nice"),
                r.get("level") or "",
            )
            for r in reqs
            if r.get("skill_name") and r["skill_name"].strip()
        ]
    return [
        (s.get("name", "").strip(), "nice", "")
        for s in (ext.get("skills") or [])
        if s.get("name") and s["name"].strip()
    ]


def build_aggregates(rows) -> dict[str, PositionAgg]:
    """从 jd_raw 已抽取记录聚合。

    rows 需为 JDRaw ORM 行（使用 row.snapshot / row.source）。
    岗位名经 normalize_position_name 归一化（与 import_jd 入图命名一致，
    否则聚合写回 MATCH {name} 匹配不上图谱节点）；空岗位名不参与聚合。
    """
    from app.services.extraction.dictionary import normalize_position_name

    agg: dict[str, PositionAgg] = defaultdict(PositionAgg)
    for row in rows:
        snap = row.snapshot or {}
        # SimHash 近似重复（设计文档 §4.2 消费方）：保留先入库版本，
        # 被标记 _duplicate_of 的后入库记录不参与聚合，避免重复 JD 虚高频次
        if snap.get("_duplicate_of"):
            continue
        ext = snap.get("extraction") or {}
        pos = normalize_position_name((ext.get("position_name") or "").strip())
        if not pos:
            continue
        pa = agg[pos]
        pa.jd_count += 1
        years = _min_experience_years(snap)
        if years is not None:
            pa.exp_years.append(years)
        source = row.source or ""
        for skill, necessity, level in _position_skills(ext):
            sa = pa.skills[skill]
            sa.hit += 1
            sa.sources.add(source)
            if level:
                sa.levels.append(level)
            if necessity == "must":
                sa.must_count += 1
        # 软技能：仅统计岗位本体白名单（JD 抽取已过滤，此处兜底再校验）
        for soft in ext.get("soft_skills") or []:
            soft = soft.strip()
            if soft in SOFT_SKILL_WHITELIST:
                pa.soft_skills[soft] += 1
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
            # 软技能按 JD 命中数降序（低频软技能不写入岗位本体）
            "soft_skills": [s for s, _ in pa.soft_skills.most_common()],
        })
        for skill, sa in pa.skills.items():
            is_must = _is_must(sa, pa.jd_count)
            edges.append({
                "pos": pos,
                "skill": skill,
                "weight": _WEIGHT_MUST if is_must else _WEIGHT_NICE,
                "necessity": "must" if is_must else "nice",
                "source_count": len(sa.sources),
                "level": _most_common_level(sa.levels),
            })

    with session:
        if positions:
            session.run(
                """
                UNWIND $items AS it
                MATCH (p:Position {name: it.pos})
                SET p.freq = it.freq,
                    p.last_updated = it.now,
                    p.required_years = coalesce(it.req_years, p.required_years),
                    p.soft_skills = it.soft_skills
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
                    r.source_count = e.source_count,
                    r.level = e.level
                """,
                edges=edges,
            )
    return {"positions": len(positions), "edges": len(edges)}
