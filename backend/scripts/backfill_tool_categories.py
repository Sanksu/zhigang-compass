# -*- coding: utf-8 -*-
"""Tool 节点分类存量回填（08-24 盘点 P2：2670 个 Tool 的 category 全空串）。

口径：与入图侧 effective_tool_category 一致——白名单词表命中写真实类目，
未命中写「未分类」哨兵（对齐 Skill 的「category 无 null/空串」不变量）。
幂等可重复执行；LLM 已给出类别的节点不覆盖。

用法：
    uv run python scripts/backfill_tool_categories.py [--dry-run]
"""

import argparse
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("backfill_tool_categories")


def build_updates(names: list[str], category_map: dict[str, str]) -> list[dict]:
    """工具名 → 回填行（纯函数）。命中词表写类目，否则写「未分类」。"""
    rows = []
    for name in names:
        if not (name or "").strip():
            continue
        rows.append({
            "name": name,
            "category": category_map.get(name) or "未分类",
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Tool 节点 category 存量回填")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()

    from app.core.database import neo4j_driver
    from app.services.extraction.dictionary import SKILL_CATEGORY

    with neo4j_driver.session() as session:
        names = [r["name"] for r in session.run(
            "MATCH (t:Tool) WHERE t.category IS NULL OR t.category = '' "
            "RETURN t.name AS name",
        )]
        rows = build_updates(names, SKILL_CATEGORY)
        matched = sum(1 for r in rows if r["category"] != "未分类")
        logger.info("待回填 %d 个 Tool（词表命中 %d，哨兵 %d）",
                    len(rows), matched, len(rows) - matched)
        if args.dry_run:
            return
        if rows:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (t:Tool {name: row.name})
                SET t.category = row.category
                """,
                rows=rows,
            )
            logger.info("回填完成：%d 个", len(rows))


if __name__ == "__main__":
    main()
