"""图谱 Position 脏节点清理（P3，2026-08-14）。

依据《position_dirty_audit_20260814.md》：112 岗位中真脏 23 个
（产品名/英文泛词/实体类型名/业务碎片——SailPoint/Seismic/Staff/
QA/Web/UX/BIOS/AI证据×2/Adobe 转型/AS400/云AI 碎片等）。

删除口径：DETACH DELETE 岗位节点（连带 REQUIRES 等边），技能节点
独立保留（岗位是聚合产物，删除不影响 Skill/Evidence/课程）。
边界 4 个（AI 性能/AI性能/云AI/仪器AIT）不在此清单——人工定后另行处理。

用法：
    uv run -- python scripts/cleanup_dirty_positions.py            # dry-run 报告
    uv run -- python scripts/cleanup_dirty_positions.py --apply    # 备份后删除
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.database import neo4j_driver

# 真脏岗位（审计 2026-08-14 确认，产品名/泛词/实体类型名/碎片）
DIRTY_POSITIONS = {
    "SailPoint", "云/AI", "Gemini 应用合作伙伴", "AI/ML与生成式AI", "BIOS",
    "AI 证据", "AI证据", "Adobe 转型高级助理", "Staff", "IT数据科学",
    "AS400 应用程序", "QA", "Seismic", "AI 生产力", "创新AI",
    "AI 与自动化", "AI/ML应用", "AI 基础设施", "Web", "UX",
    "投诉处理助理", "AI/ML", "Web内容平台",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="实际删除（默认 dry-run）")
    args = parser.parse_args()

    with neo4j_driver.session() as session:
        recs = session.run(
            """
            MATCH (p:Position) WHERE p.name IN $names
            RETURN p.name AS name,
                   COUNT { (p)-[:REQUIRES]->() } AS edges,
                   COUNT { (p)<-[:EVIDENCED_BY]-() } AS ev
            ORDER BY edges DESC
            """,
            names=list(DIRTY_POSITIONS),
        ).data()
        found = [r for r in recs]
        missing = DIRTY_POSITIONS - {r["name"] for r in found}

    total_edges = sum(r["edges"] for r in found)
    print(f"待删岗位 {len(found)} 个（连带 REQUIRES 边 {total_edges} 条）")
    if missing:
        print(f"⚠️ 清单中未找到: {sorted(missing)}")
    for r in found:
        print(f"  边{r['edges']:3d} 证据{r['ev']:3d}  {r['name']}")

    if not args.apply:
        print("\ndry-run：未删除（--apply 才执行）")
        return 0

    backup = ROOT / "reports" / f"dirty_positions_deleted_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    backup.parent.mkdir(parents=True, exist_ok=True)
    with open(backup, "w", encoding="utf-8") as f:
        for r in found:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"备份: {backup}")

    with neo4j_driver.session() as session:
        for r in found:
            session.run("MATCH (p:Position {name: $n}) DETACH DELETE p", n=r["name"])
    print(f"已删除 {len(found)} 个脏岗位节点")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
