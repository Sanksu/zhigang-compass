"""图谱 LEARNABLE_VIA 语义脏边审计（P3，2026-08-13）。

背景：学习路径 30 案例评审发现课程误配三层根因之一——图谱脏边
（Unix Shell→Genomic Data Science sim 0.013、Servers→Node.js 0.105、
Technical Documentation→C++ 0.253 等），课程入图时技能匹配错。
运行时课程名门控（courses.py ≥0.5）已防推荐误导，但脏边仍占用
"有课程的技能"池并污染技能级 fallback。

本脚本全量审计 LEARNABLE_VIA 边（skill.name ↔ course.name 语义相似度）：
- 分档：<0.30 严重脏（推荐清理）/ 0.30-0.45 可疑（人工复核）/ ≥0.45 正常
- 统计脏技能节点（skill 侧全部边均 <0.45 = 该技能疑似脏技能或全脏边）
- 输出审计报告（默认 --dry-run 只报告；--apply 才删边）

用法：
    uv run -- python scripts/audit_learnable_via.py              # dry-run 报告
    uv run -- python scripts/audit_learnable_via.py --threshold 0.35
    uv run -- python scripts/audit_learnable_via.py --apply      # 删除严重脏边
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.database import neo4j_driver

# 分档阈值：< SEVERE 严重脏（建议清理）；SEVERE~SUSPICIOUS 可疑（人工复核）
SEVERE = 0.30
SUSPICIOUS = 0.45


def load_edges() -> list[dict]:
    with neo4j_driver.session() as session:
        rows = session.run(
            """
            MATCH (s:Skill)-[r:LEARNABLE_VIA]->(c:Course)
            RETURN s.name AS skill, c.name AS course, c.source AS source,
                   elementId(r) AS rel_id
            """
        ).data()
    return rows


def audit(edges: list[dict], semantic) -> dict:
    """全量语义审计，返回分档统计与清单。

    相似度计算异常（模型中途不可用等）记 error 档——不落入严重档，
    避免 --apply 误删。
    """
    severe, suspicious, normal, errors = [], [], [], []
    for e in edges:
        try:
            sim = semantic.similarity(e["skill"], e["course"])
        except Exception:
            e["sim"] = None
            errors.append(e)
            continue
        e["sim"] = round(sim, 3)
        if sim < SEVERE:
            severe.append(e)
        elif sim < SUSPICIOUS:
            suspicious.append(e)
        else:
            normal.append(e)

    # 脏技能节点：skill 侧全部边 < SUSPICIOUS（该技能所有课程均语义无关）
    skill_sims: dict[str, list[float]] = {}
    for e in edges:
        if e.get("sim") is not None:
            skill_sims.setdefault(e["skill"], []).append(e["sim"])
    dirty_skills = {
        s: sims for s, sims in skill_sims.items()
        if sims and all(v < SUSPICIOUS for v in sims)
    }

    return {
        "total_edges": len(edges),
        "severe_count": len(severe),
        "suspicious_count": len(suspicious),
        "normal_count": len(normal),
        "error_count": len(errors),
        "severe_ratio": round(len(severe) / len(edges), 4) if edges else 0,
        "dirty_skill_count": len(dirty_skills),
        "severe_edges": sorted(severe, key=lambda e: e["sim"])[:60],
        "suspicious_edges": sorted(suspicious, key=lambda e: e["sim"])[:30],
        "dirty_skills": dict(sorted(dirty_skills.items(), key=lambda kv: min(kv[1]))[:30]),
        "errors": errors[:20],
    }


def delete_severe(edges: list[dict], threshold: float) -> int:
    """删除 sim < threshold 的边（--apply），按 elementId 精确删除防同名多删。

    删除前导出全量待删清单备份（reports/learnable_via_deleted_*.jsonl）。
    """
    targets = [e for e in edges if e.get("sim") is not None and e["sim"] < threshold]
    backup = ROOT / "reports" / f"learnable_via_deleted_{__import__('time').strftime('%Y%m%d_%H%M%S')}.jsonl"
    backup.parent.mkdir(parents=True, exist_ok=True)
    with open(backup, "w", encoding="utf-8") as f:
        for e in targets:
            f.write(json.dumps({"skill": e["skill"], "course": e["course"],
                                "sim": e["sim"], "rel_id": e["rel_id"]}, ensure_ascii=False) + "\n")
    print(f"待删 {len(targets)} 条已备份至 {backup}")

    with neo4j_driver.session() as session:
        deleted = 0
        for e in targets:
            session.run(
                "MATCH ()-[r]->() WHERE elementId(r) = $rid DELETE r",
                rid=e["rel_id"],
            )
            deleted += 1
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=SEVERE,
                        help="清理阈值（sim < 阈值删除，默认 0.30 严重档）")
    parser.add_argument("--apply", action="store_true", help="实际删除（默认 dry-run）")
    parser.add_argument("--out", type=Path, default=ROOT / "reports" / "learnable_via_audit.json")
    args = parser.parse_args()

    from app.services.matching.semantic import SkillEmbedder
    semantic = SkillEmbedder.get()
    print("SBERT 已加载，全量审计 LEARNABLE_VIA 边……")

    edges = load_edges()
    result = audit(edges, semantic)
    print(f"边总数: {result['total_edges']} | 严重脏 <{args.threshold}: {result['severe_count']} "
          f"({result['severe_ratio']:.1%}) | 可疑: {result['suspicious_count']} | 正常: {result['normal_count']}")
    print(f"疑似脏技能节点（全部边均 <0.45）: {result['dirty_skill_count']}")

    print("\n=== 严重脏边 Top-15 ===")
    for e in result["severe_edges"][:15]:
        print(f"  {e['sim']:.3f}  {e['skill'][:20]} → {e['course'][:40]}")

    print("\n=== 疑似脏技能 Top-10 ===")
    for s, sims in list(result["dirty_skills"].items())[:10]:
        print(f"  min={min(sims):.3f}  {s[:30]} ({len(sims)} 边)")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n审计报告已输出 {args.out}")

    if args.apply:
        n = delete_severe(edges, args.threshold)
        print(f"已删除 {n} 条 sim < {args.threshold} 的脏边")
    else:
        print("dry-run：未删除（--apply 才执行清理）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
