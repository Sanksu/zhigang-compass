"""课程质量评估脚本（DA-M4-01，设计文档 §4.6）。

遍历 course_raw 全量课程 → 六维加权质量评分 → 幂等写回
`snapshot["quality"]`（覆盖更新，不重复累积）。输出推荐池统计。

用法：
    python scripts/evaluate_courses.py              # 全量评估并写回
    python scripts/evaluate_courses.py --no-write   # 仅评估预览，不写库
    python scripts/evaluate_courses.py --top 20     # 输出质量分 Top-N 课程
"""

import argparse
import asyncio
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.raw import CourseRaw
from app.services.data_quality.course_quality import (
    RECOMMEND_MIN_SCORE,
    evaluate_course,
)


async def main(write: bool, top: int) -> None:
    async with async_session_factory() as session:
        rows = (await session.scalars(select(CourseRaw).order_by(CourseRaw.id.asc()))).all()

        results = []
        for row in rows:
            snap = dict(row.snapshot or {})
            result = evaluate_course(snap)
            results.append((row, result))
            if write:
                snap["quality"] = result.model_dump()
                row.snapshot = snap
        if write:
            await session.commit()

    recommended = [r for _, r in results if r.recommended]
    print("=" * 64)
    print("课程质量评估报告（DA-M4-01）")
    print("=" * 64)
    print(f"课程总数: {len(results)} | 推荐池（≥{RECOMMEND_MIN_SCORE}）: {len(recommended)}")
    if not write:
        print("预览模式（--no-write）：未写库")
    if top:
        print(f"\n质量分 Top-{min(top, len(results))}:")
        for _, r in sorted(results, key=lambda x: x[1].quality_score, reverse=True)[:top]:
            flag = "★推荐" if r.recommended else "   "
            print(
                f"  {flag} {r.quality_score:.3f} | {r.platform:<10} | "
                f"{(r.title or '')[:24]}"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="课程质量评估")
    parser.add_argument("--no-write", action="store_true", help="仅评估预览，不写库")
    parser.add_argument("--top", type=int, default=10, help="输出质量分 Top-N 课程")
    args = parser.parse_args()
    asyncio.run(main(write=not args.no_write, top=args.top))
