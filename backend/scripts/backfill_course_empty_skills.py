"""课程空技能滞后重抽前置回填（08-31）。

背景：enrich_course_skills 旧逻辑对"LLM 正常判定无技能"的课程直接写
skills_enriched=true 永久标记，导致这些课程永不重抽（存量大量为技能空数组）。
本次引入滞后重抽（24h 冷却 + 上限后放弃）后，需先把已永久标记但技能为空的
课程重置——清除 skills_enriched / skills_retry_at，使其重新进入滞后重试点。

安全设计：
- 默认 dry-run：只读扫描并打印受影响课程数 + 示例，不写库。
- --apply：单事务内仅对"技能为空 且 skills_enriched=true"的行做条件
  jsonb 删键（去掉 skills_enriched 与 skills_retry_at，保留 skills_enrich_fails
  供排查）。幂等：重复执行因 skills_enriched 已清除而不命中目标。
- 绝不写入 skills；只为重抽让路。

用法（cwd=backend）：
    python -m scripts.backfill_course_empty_skills                 # dry-run
    python -m scripts.backfill_course_empty_skills --apply         # 实际重置
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select

from app.core.database import async_session_factory, engine
from app.models.raw import CourseRaw

_MAX_SAMPLE = 10


def _empty_predicate():
    """技能为空（null / 非数组 / 空数组）。"""
    return (
        (CourseRaw.snapshot["skills"].astext.is_(None))
        | (func.jsonb_typeof(CourseRaw.snapshot["skills"]) != "array")
        | (func.jsonb_array_length(CourseRaw.snapshot["skills"]) == 0)
    )


async def _targets() -> list[dict]:
    """只读定位待重置课程（技能为空 且 已永久标记 skills_enriched）。"""
    async with async_session_factory() as session:
        rows = (
            await session.scalars(
                select(CourseRaw)
                .where(
                    _empty_predicate(),
                    CourseRaw.snapshot["skills_enriched"].astext == "true",
                )
                .order_by(CourseRaw.id.asc())
            )
        ).all()
    return [
        {
            "id": r.id,
            "source": r.source,
            "source_id": r.source_id,
            "title": (r.snapshot or {}).get("title", ""),
            "enrich_fails": (r.snapshot or {}).get("skills_enrich_fails"),
        }
        for r in rows
    ]


async def dry_run() -> int:
    targets = await _targets()
    print(f"待重置课程 {len(targets)} 门（技能为空且 skills_enriched=true）")
    for t in targets[:_MAX_SAMPLE]:
        print(
            f"  id={t['id']} source={t['source']}/{t['source_id']} "
            f"title={t['title'][:40]!r} fails={t['enrich_fails']}"
        )
    if len(targets) > _MAX_SAMPLE:
        print(f"  … 其余 {len(targets) - _MAX_SAMPLE} 门未列出")
    return len(targets)


async def apply() -> int:
    targets = await _targets()
    ids = [t["id"] for t in targets]
    if not ids:
        print("无待重置课程，跳过")
        return 0
    # 条件删除 skills_enriched / skills_retry_at（保留 skills_enrich_fails 供排查）
    # 幂等：仅命中 still skills_enriched=true 的行
    statement = (
        "UPDATE course_raw "
        "SET snapshot = snapshot - 'skills_enriched' - 'skills_retry_at', "
        "    updated_at = now() "
        "WHERE id = ANY(:ids) "
        "  AND snapshot->>'skills_enriched' = 'true'"
    )
    async with engine.begin() as conn:
        result = await conn.execute(statement, {"ids": ids})
        updated = result.rowcount
    print(f"重置完成: 更新 {updated}/{len(ids)} 门课程（已清除 skills_enriched/retry_at）")
    return updated


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="执行重置（默认 dry-run 只读）")
    args = parser.parse_args()
    asyncio.run(apply() if args.apply else dry_run())


if __name__ == "__main__":
    main()