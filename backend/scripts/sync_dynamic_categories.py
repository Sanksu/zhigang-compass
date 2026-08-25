"""技能分类审批落图同步脚本（PR 补：skill_classify 的权威 category 晋升执行器）。

读取 skill_category_approvals（admin approve 后的事实源）→ 把图谱
Skill.category 幂等 SET 为批准值。对齐 skill_relation 的「approve 写 PG、
独立 sync 脚本写图」原则——图写入不在 API 端点内发生。

- 幂等：SET（重复执行安全），applied_to_graph 仅标记进度（失败可重跑）
- 节点缺失：Skill 节点不存在则跳过（幂等无害）
- 与 YAML/白名单权威分类并立：本表是 LLM 提议 + 人工审批的第 2 事实源

用法：
    uv run python scripts/sync_dynamic_categories.py [--dry-run]
"""

import argparse
import asyncio
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("sync_dynamic_categories")


def apply_categories(
    rows: list[dict],
    neo4j_session,
    dry_run: bool = False,
) -> dict:
    """逐条把 Skill.category 晋升为批准值（幂等 SET）；返回统计。"""
    stats = {"merged": 0, "skipped_no_node": 0}
    for row in rows:
        skill_name = str(row.get("skill_name") or "").strip()
        category = str(row.get("category") or "").strip()
        if not skill_name or not category:
            stats["skipped_no_node"] += 1
            continue
        if dry_run:
            stats["merged"] += 1
            continue
        result = neo4j_session.run(
            "MATCH (s:Skill {name: $name}) SET s.category = $category "
            "RETURN s.name AS name",
            name=skill_name, category=category,
        )
        if result.single() is None:
            stats["skipped_no_node"] += 1
            logger.warning("[sync_categories] 技能节点缺失，跳过: %s", skill_name)
            continue
        stats["merged"] += 1
    return stats


async def _load_approved_rows() -> list[dict]:
    """skill_category_approvals 全部行（approve 即插，未同步的经 applied 标记忽略）。"""
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.business import SkillCategoryApproval

    async with async_session_factory() as session:
        rows = (await session.scalars(select(SkillCategoryApproval))).all()
        return [
            {"skill_name": r.skill_name, "category": r.category, "id": str(r.id)}
            for r in rows
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description="技能分类审批落图（先审批后落图）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写图")
    args = parser.parse_args()

    from app.core.database import neo4j_driver

    rows = asyncio.run(_load_approved_rows())
    with neo4j_driver.session() as session:
        stats = apply_categories(rows, session, dry_run=args.dry_run)
    logger.info(
        "技能分类落图%s: %s", "（dry-run）" if args.dry_run else "", stats,
    )
    print(stats)


if __name__ == "__main__":
    main()