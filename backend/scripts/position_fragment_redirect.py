"""岗位名碎片存量归位（2026-08-16 P11 岗位处置，一次性脚本）。

词典映射（dictionary.py _POSITION_KEYWORDS / _EN_POSITION_MAP，P11 批次）
负责防复发；本脚本处理存量图谱节点：
- 改名：目标规范名在图谱不存在（UX→UX设计师、CFD分析→CFD分析工程师、
  仪器AIT→仪器AIT工程师、OBD标定→OBD标定工程师、SAP 技术管理员→SAP技术管理员）
- 合并：目标规范名已存在（重连 REQUIRES/HAS_EVIDENCE 后删除旧节点，复用
  position_duplicate_cleanup._merge_graph）：Staff→后端开发工程师、
  FPGA团队→FPGA验证工程师、Kubernetes与OpenShift→DevOps工程师、
  Endur技术→后端开发工程师、STEM课程→STEM科技教育讲师、TAK→移动开发工程师
- IT 保留（T-04 决策：裸 IT 不拦——防误伤 IT系统管理员/IT经理）

备份：reports/position_fragment_redirect_{date}.jsonl（dry-run 也写）。
用法（cwd=backend）：
    python -m scripts.position_fragment_redirect            # dry-run 报告
    python -m scripts.position_fragment_redirect --apply    # 执行
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.database import neo4j_driver
from app.core.logging import setup_logging
from scripts.position_duplicate_cleanup import _merge_graph

logger = setup_logging("position_fragment_redirect")

# 目标不存在 → 改名；目标存在 → 合并
RENAMES: dict[str, str] = {
    "UX": "UX设计师",
    "CFD分析": "CFD分析工程师",
    "仪器AIT": "仪器AIT工程师",
    "OBD标定": "OBD标定工程师",
    "SAP 技术管理员": "SAP技术管理员",
}
MERGES: dict[str, str] = {
    "Staff": "后端开发工程师",
    "FPGA团队": "FPGA验证",
    "Kubernetes与OpenShift": "DevOps工程师",
    "Endur技术": "后端开发工程师",
    "STEM课程": "STEM科技教育讲师",
    "TAK": "移动开发工程师",
}


def _backup(rows: list[dict], name: str) -> Path:
    date = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = ROOT / "reports" / f"position_fragment_redirect_{name}_{date}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    logger.info("  备份: %s（%s 条）", path, len(rows))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="岗位名碎片存量归位（P11，一次性）")
    parser.add_argument("--apply", action="store_true", help="执行归位（默认 dry-run 只报告+备份）")
    args = parser.parse_args(argv)

    logger.info("=" * 60)
    logger.info("岗位名碎片存量归位 %s", "[执行]" if args.apply else "[dry-run]")
    logger.info("=" * 60)

    all_plans: dict[str, str] = {**RENAMES, **MERGES}
    with neo4j_driver.session() as session:
        rows = session.run(
            "MATCH (p:Position) WHERE p.name IN $names "
            "RETURN p.name AS name, p.id AS id",
            names=list(all_plans),
        ).data()
    by_name = {r["name"]: r["id"] for r in rows}
    targets = session_run_targets(all_plans)

    plans: list[dict] = []
    for old, new in all_plans.items():
        if old not in by_name:
            continue
        if new in targets:
            plans.append({"action": "merge", "old": old, "target": new,
                          "old_id": by_name[old], "target_id": targets[new]})
        else:
            plans.append({"action": "rename", "old": old, "target": new,
                          "old_id": by_name[old], "target_id": None})

    logger.info("  计划: %s", {p["action"]: sum(1 for x in plans if x["action"] == p["action"]) for p in plans})
    if not plans:
        logger.info("  无存量节点待归位（幂等）")
        return 0
    _backup(plans, "plans")
    if not args.apply:
        logger.info("  (dry-run，--apply 才执行)")
        return len(plans)

    with neo4j_driver.session() as session:
        for p in plans:
            if p["action"] == "rename":
                session.run(
                    "MATCH (p:Position {id: $id}) SET p.name = $new",
                    id=p["old_id"], new=p["target"],
                )
            else:
                _merge_graph(session, p["target_id"], [p["old_id"]], p["target"])
    logger.info("  已归位 %s 个节点", len(plans))
    return len(plans)


def session_run_targets(all_plans: dict[str, str]) -> dict[str, str]:
    """查目标规范名在图谱的 id（含目标名带空格变体，如 SAP技术管理员）。"""
    with neo4j_driver.session() as session:
        rows = session.run(
            "MATCH (p:Position) WHERE p.name IN $names RETURN p.name AS name, p.id AS id",
            names=list(set(all_plans.values())),
        ).data()
    return {r["name"]: r["id"] for r in rows}


if __name__ == "__main__":
    raise SystemExit(main())
