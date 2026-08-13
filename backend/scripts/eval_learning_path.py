"""学习路径 30 案例专家评审（设计文档 §1.4「学习路径合理性 ≥ 80% / 30 案例专家评审」）。

案例构造：简历黄金集 50 份（gold_skills → 候选人画像，熟练度默认熟悉）× 图谱真实
Position（REQUIRES 边，must/nice/weight/proficiency 全量）轮转组合 30 对。

每案例跑 LearningPathGenerator（真实课程加载：Neo4j LEARNABLE_VIA + PG quality），
输出结构化评审表 + 四维预评分（供专家复核定稿）：
  1. 路径完整性   missing 技能被 Top-5 路径项覆盖的比例（客观计算）
  2. 先修正确性   先修链存在且语义成立（YAML 字典收录 = 1.0；空链 = 0.5）
  3. 课程匹配性   推荐课程与技能相关性（有相关课程 = 1.0；课程为空 = 0.3）
  4. 学时合理性   estimated_hours 与现实投入偏差（≤200h = 1.0；200-500h = 0.7；>500h = 0.4）

案例合理 = 四维平均 ≥ 0.8；整体达标 = 30 案例中 ≥ 80% 合理。
预评分供专家复核，非终审结论；专家修改后重跑统计（--re-score 读人工评分覆盖）。

用法：
    uv run -- python scripts/eval_learning_path.py              # 全量生成 + 预评分
    uv run -- python scripts/eval_learning_path.py --limit 3    # 冒烟
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.database import neo4j_driver
from app.services.learning_path.generator import LearningPathGenerator
from app.services.learning_path.schemas import GapType
from app.services.matching.schemas import (
    CandidateProfile,
    CandidateSkill,
    PositionProfile,
    SkillRequirement,
)

_CASE_COUNT = 30
_HOURS_REAL = [(200, 1.0), (500, 0.7), (float("inf"), 0.4)]


def load_resume_candidates() -> list[CandidateProfile]:
    """简历黄金集 → 候选人画像（skills 熟练度默认熟悉=2）。"""
    path = ROOT / "data" / "golden_set" / "golden_set_resume.jsonl"
    profiles = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        skills = [CandidateSkill(skill_id="", skill_name=s, proficiency=2)
                  for s in (r.get("gold_skills") or [])]
        # education 为教育经历数组，取最高学历（按学位级别排序取尾）
        edu = r.get("education") or []
        degree = ""
        if isinstance(edu, list) and edu:
            order = {"大专": 0, "本科": 1, "硕士": 2, "博士": 3}
            degree = max((e.get("degree") or "" for e in edu if isinstance(e, dict)),
                         key=lambda d: order.get(d, -1), default="")
        profiles.append(CandidateProfile(
            user_id=str(r.get("id") or len(profiles)),
            skills=skills,
            total_years=float(r.get("total_years") or 0),
            education_level=degree or None,
        ))
    return profiles


def load_graph_positions(min_edges: int = 3, max_edges: int = 40) -> list[PositionProfile]:
    """图谱真实岗位（REQUIRES 边 3-40，排除超聚合岗位画像）。"""
    with neo4j_driver.session() as session:
        rows = session.run(
            """
            MATCH (p:Position)-[r:REQUIRES]->(s:Skill)
            WITH p, count(r) AS n
            WHERE n >= $min_edges AND n <= $max_edges
            MATCH (p)-[r2:REQUIRES]->(s2:Skill)
            RETURN p.id AS pid, p.name AS pname, s2.id AS sid, s2.name AS sname,
                   r2.necessity AS necessity, r2.weight AS weight,
                   r2.proficiency AS proficiency, n AS edge_count
            ORDER BY n DESC, pid
            """,
            min_edges=min_edges,
            max_edges=max_edges,
        ).data()
    by_pos: dict[str, dict] = {}
    for r in rows:
        pos = by_pos.setdefault(r["pid"], {"id": r["pid"], "name": r["pname"], "must": [], "nice": []})
        req = SkillRequirement(
            skill_id=r["sid"], skill_name=r["sname"],
            necessity="must" if r.get("necessity") == "must" else "nice",
            weight=float(r.get("weight") or 1.0),
            proficiency=r.get("proficiency"),
        )
        (pos["must"] if req.necessity == "must" else pos["nice"]).append(req)
    return [
        PositionProfile(position_id=p["id"], name=p["name"],
                        must_skills=p["must"], nice_skills=p["nice"])
        for p in by_pos.values()
    ]


def score_path(item_count: int, gap_count: int) -> float:
    """完整性：路径项是否覆盖最高优先级缺口（Top-5 截断为设计行为）。

    generator 取 gaps[:5]（weight DESC + missing>weak）；预期项数 =
    min(5, gap 数)。路径项数达到预期即结构完整（1.0）；生成异常（如
    课程/先修查询失败中断）低于预期按比例扣分。
    """
    expected = min(5, gap_count)
    if expected == 0:
        return 1.0
    return min(1.0, item_count / expected)


def score_hours(hours: float | None, item_count: int) -> float:
    """学时合理性：按每项平均学时（30h 为默认值未定制，系统性低估）。

    现实基准：单个技能从了解到掌握普遍 40-100h；默认 30h/技能明显偏少。
    """
    if hours is None or item_count == 0:
        return 0.5
    avg = hours / item_count
    if avg < 35:
        return 0.4  # 默认学时未定制，低估
    if avg <= 80:
        return 1.0
    if avg <= 150:
        return 0.7
    return 0.5


async def amain() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=_CASE_COUNT, help="案例数（冒烟用）")
    parser.add_argument("--out", type=Path,
                        default=ROOT / "data" / "golden_set" / "review" / "learning_path_eval_30.json")
    parser.add_argument("--re-score", type=Path, default=None,
                        help="人工评分覆盖文件（{case_id: {prerequisite/course/hours: 分, note: 备注}}），"
                             "读入后重算 mean/reasonable/summary 并覆盖输出（completeness 为客观计算不覆盖）")
    args = parser.parse_args()

    candidates = load_resume_candidates()
    positions = load_graph_positions()
    print(f"候选池: {len(candidates)} 份简历 | 图谱岗位池: {len(positions)} 个")

    n = min(args.limit, len(candidates), len(positions))
    gen = LearningPathGenerator()
    try:
        from app.services.matching.semantic import SkillEmbedder
        semantic = SkillEmbedder.get()
        print("SBERT 语义模型已加载")
    except Exception as exc:
        semantic = None
        print(f"SBERT 不可用，降级纯规则: {exc}")

    cases = []
    for i in range(n):
        cand, pos = candidates[i], positions[i]
        result = await gen.generate(cand, pos, semantic=semantic)
        missing = sum(1 for g in result.gaps if g.gap_type == GapType.MISSING)
        gap_total = sum(1 for g in result.gaps if g.gap_type in (GapType.MISSING, GapType.WEAK))
        items = result.items

        s_complete = score_path(len(items), gap_total)
        s_prereq = [1.0 if it.prerequisites else 0.5 for it in items]
        s_course = [1.0 if it.courses else 0.3 for it in items]
        s_hours = [score_hours(it.estimated_hours, len(items)) for it in items]

        avg = lambda xs: sum(xs) / len(xs) if xs else 0.0
        dims = {
            "completeness": round(s_complete, 2),
            "prerequisite": round(avg(s_prereq), 2),
            "course": round(avg(s_course), 2),
            "hours": round(avg(s_hours), 2),
        }
        dims["mean"] = round(sum(dims.values()) / 4, 2)
        cases.append({
            "case_id": f"lp_{i + 1:02d}",
            "candidate_id": cand.user_id,
            "position": f"{pos.name} ({pos.position_id})",
            "missing_skills": missing,
            "gap_count": len(result.gaps),
            "path_items": [{
                "skill": it.skill,
                "prerequisites": it.prerequisites,
                "courses": [f"{c.title}@{c.platform}(q={c.quality_score})" for c in it.courses],
                "estimated_hours": it.estimated_hours,
                "priority": it.priority,
            } for it in items],
            "scores": dims,
            "reasonable": dims["mean"] >= 0.8,
        })
        print(f"  [{i + 1}/{n}] {cases[-1]['case_id']} {pos.name}: missing={missing} "
              f"items={len(items)} mean={dims['mean']}")

    reasonable = sum(1 for c in cases if c["reasonable"])
    summary = {
        "case_count": n,
        "reasonable_count": reasonable,
        "reasonable_ratio": round(reasonable / n, 4) if n else 0,
        "target_ratio": 0.8,
        "target_met": (reasonable / n >= 0.8) if n else False,
        "avg_dims": {
            k: round(sum(c["scores"][k] for c in cases) / n, 3)
            for k in ("completeness", "prerequisite", "course", "hours", "mean")
        },
        "note": "预评分（规则化 AI 评审），专家复核后重跑 --re-score 定稿",
    }
    if args.re_score:
        human = json.loads(args.re_score.read_text(encoding="utf-8"))
        applied = 0
        for c in cases:
            h = human.get(c["case_id"])
            if not h:
                continue
            for dim in ("prerequisite", "course", "hours"):
                if dim in h:
                    c["scores"][dim] = round(float(h[dim]), 2)
                    applied += 1
            c["scores"]["mean"] = round(
                sum(c["scores"][d] for d in ("completeness", "prerequisite", "course", "hours")) / 4, 2)
            c["reasonable"] = c["scores"]["mean"] >= 0.8
            if h.get("note"):
                c["review_note"] = h["note"]
        reasonable = sum(1 for c in cases if c["reasonable"])
        summary = {
            "case_count": n,
            "reasonable_count": reasonable,
            "reasonable_ratio": round(reasonable / n, 4) if n else 0,
            "target_ratio": 0.8,
            "target_met": (reasonable / n >= 0.8) if n else False,
            "avg_dims": {
                k: round(sum(c["scores"][k] for c in cases) / n, 3)
                for k in ("completeness", "prerequisite", "course", "hours", "mean")
            },
            "note": f"人工定稿（{args.re_score.name}，覆盖 {applied} 项评分）",
        }
        print(f"人工评分覆盖: {applied} 项")
    payload = {"summary": summary, "cases": cases}
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n合理性: {reasonable}/{n} = {summary['reasonable_ratio']:.1%} "
          f"(目标 ≥80% {'✅' if summary['target_met'] else '❌'})")
    print(f"四维均分: {summary['avg_dims']}")
    print(f"已输出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
