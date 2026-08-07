"""清理 jd_raw SimHash 近似重复（_duplicate_of）存量，并同步清理图谱证据。

背景：CleaningPipeline 采集时对近似重复 JD 打 `_duplicate_of` 标记（保留先
入库版本），聚合（build_aggregates）已忽略重复版本，但 jd_raw 行仍保留——
占位存储 + 详情回填浪费请求。本脚本删除重复行 + 图谱对应 Evidence + 重算聚合。

清理范围：source='zhilian' 且 snapshot 含 `_duplicate_of` 的记录（保留版本不动）。

使用：
    uv run python scripts/cleanup_duplicate_jds.py --dry-run   # 仅报告，不删除
    uv run python scripts/cleanup_duplicate_jds.py             # 执行
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import delete, select  # noqa: E402

from app.core.database import async_session_factory, neo4j_driver  # noqa: E402
from app.models.raw import JDRaw  # noqa: E402
from app.services.kg.aggregation import build_aggregates, write_aggregates  # noqa: E402


async def _duplicate_rows() -> list[JDRaw]:
    """zhilian 中带 _duplicate_of 标记的重复记录。"""
    async with async_session_factory() as s:
        return list((await s.scalars(
            select(JDRaw).where(
                JDRaw.source == "zhilian",
                JDRaw.snapshot["_duplicate_of"].astext.isnot(None),
            )
        )).all())


def _delete_evidence(urls: list[str]) -> int:
    """删除指定 source_url 的 Evidence 节点（DETACH 连带删除关联边）。"""
    with neo4j_driver.session() as session:
        before = session.run(
            "MATCH (e:Evidence) WHERE e.source_url IN $urls RETURN count(e) AS c", urls=urls
        ).single()["c"]
        session.run(
            "MATCH (e:Evidence) WHERE e.source_url IN $urls DETACH DELETE e", urls=urls
        )
        return before


async def _delete_jd_rows(ids: list[int]) -> None:
    async with async_session_factory() as s:
        await s.execute(delete(JDRaw).where(JDRaw.id.in_(ids)))
        await s.commit()


async def _reaggregate() -> dict:
    """从剩余 jd_raw 重算岗位聚合（幂等覆盖）。"""
    async with async_session_factory() as s:
        rows = (await s.scalars(select(JDRaw))).all()
    agg = build_aggregates(rows)
    now = datetime.now(timezone.utc).isoformat()
    with neo4j_driver.session() as session:
        return write_aggregates(session, agg, now)


async def main() -> None:
    parser = argparse.ArgumentParser(description="清理 jd_raw SimHash 重复（jd_raw + 图谱证据 + 重聚合）")
    parser.add_argument("--dry-run", action="store_true", help="仅报告，不删除")
    args = parser.parse_args()

    rows = await _duplicate_rows()
    urls = sorted({r.source_url for r in rows if r.source_url})
    print(f"zhilian 重复记录: {len(rows)} 条（涉及 source_url {len(urls)} 个）")

    with neo4j_driver.session() as session:
        ev_count = session.run(
            "MATCH (e:Evidence) WHERE e.source_url IN $urls RETURN count(e) AS c", urls=urls
        ).single()["c"]
    print(f"图谱将删除 Evidence: {ev_count} 个")

    if args.dry_run:
        print("\n[dry-run] 未执行任何删除")
        return

    deleted = _delete_evidence(urls)
    print(f"[1/3] 已删除图谱 Evidence: {deleted} 个")

    ids = [r.id for r in rows]
    await _delete_jd_rows(ids)
    print(f"[2/3] 已删除 jd_raw: {len(ids)} 条")

    result = await _reaggregate()
    print(f"[3/3] 岗位聚合重算完成: {result}")


if __name__ == "__main__":
    asyncio.run(main())
