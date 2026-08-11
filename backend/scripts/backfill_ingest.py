"""回填入图：补齐 jd_raw 剩余 LLM 抽取入图 + course_raw 全量入图。

背景：jd_raw 仅黄金集 100 条被抽取（覆盖率 12.1%），course_raw 从未入图。
本脚本补齐：
- 阶段 1：循环调用 batch_extract（每批 100 条）直到无未抽取 JD
- 阶段 2：遍历 course_raw 调 import_course 入图

幂等性：batch_extract 依据 snapshot[extraction] 标记跳过已抽取记录；
import_jd / import_course 均为 Neo4j MERGE 语义，重复执行不产生重复节点。

用法：
    python scripts/backfill_ingest.py
"""

import asyncio
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("backfill_ingest")

from sqlalchemy import func, select

from app.core.database import async_session_factory, neo4j_driver
from app.models.raw import CourseRaw, JDRaw
from app.services.kg.kg_service import import_course
from app.workers.tasks import batch_extract

# 每批条数（tasks.py 注释：批量过大易触发 provider 限流，100 条/批实测稳定）
_BATCH_SIZE = 100
_MAX_ROUNDS = 15  # 安全上限，防异常死循环


async def _remaining_unextracted() -> int:
    async with async_session_factory() as s:
        return (
            await s.scalar(
                select(func.count())
                .select_from(JDRaw)
                .where(JDRaw.snapshot["extraction"].astext.is_(None))
            )
            or 0
        )


async def backfill_jd() -> dict:
    """循环批量抽取剩余 JD 并入图，直到无未抽取记录。"""
    rounds = 0
    succeeded = 0
    while rounds < _MAX_ROUNDS:
        rounds += 1
        r = await batch_extract({}, limit=_BATCH_SIZE)
        succeeded += r["succeeded"]
        logger.info(
            "[round %s] processed=%s succeeded=%s failed=%s",
            rounds, r["processed"], r["succeeded"], len(r["failed"]),
        )
        if r["processed"] == 0:
            break
    remaining = await _remaining_unextracted()
    return {"rounds": rounds, "succeeded_total": succeeded, "remaining": remaining}


async def backfill_courses() -> tuple[int, list[str]]:
    """course_raw 全量入图（含无 skills 字段的课程，仅建 Course 节点）。"""
    async with async_session_factory() as s:
        rows = (await s.scalars(select(CourseRaw))).all()
    data = [dict(c.snapshot or {}) for c in rows]

    imported = 0
    errors: list[str] = []
    with neo4j_driver.session() as session:
        for course_data in data:
            try:
                import_course(session, course_data)
                imported += 1
            except Exception as e:
                errors.append(f"{course_data.get('source_id', '?')}: {str(e)[:200]}")
    return imported, errors


async def main() -> None:
    logger.info("=== 阶段 1：JD 剩余抽取入图 ===")
    jd = await backfill_jd()
    logger.info("JD 回填完成: rounds=%s 新增成功=%s 剩余未抽取=%s",
                jd["rounds"], jd["succeeded_total"], jd["remaining"])

    logger.info("=== 阶段 2：课程入图 ===")
    imported, errors = await backfill_courses()
    logger.info("课程入图 %s 条，失败 %s 条", imported, len(errors))
    for e in errors[:10]:
        logger.warning("COURSE FAIL: %s", e)


if __name__ == "__main__":
    asyncio.run(main())
