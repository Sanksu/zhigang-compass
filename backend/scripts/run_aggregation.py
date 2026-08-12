"""执行岗位聚合并写回 Neo4j（build_aggregates + write_aggregates）。

背景：zhilian 回填 77 条岗位名 + 1728 条重入图后，Position 节点已更新，但
聚合层（freq/typical_scenarios/REQUIRES 权重）尚未对这批新数据生效。本脚本
复用生产聚合链路（tasks.aggregate_positions 同一实现）执行一次聚合：

- build_aggregates：聚合全部已抽取 JD（岗位级 freq、软技能、典型场景、技能边）
- write_aggregates：写回 Neo4j（freq、typical_scenarios、REQUIRES 权重/necessity）

幂等：聚合是全量重算覆盖写，重复执行结果一致。
用法：cd backend && python -m scripts.run_aggregation
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.database import async_session_factory, neo4j_driver
from app.models.raw import JDRaw
from app.services.kg.aggregation import build_aggregates, write_aggregates

logging.getLogger("app").setLevel(logging.ERROR)


async def _run() -> None:
    async with async_session_factory() as session:
        rows = (await session.scalars(
            select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        )).all()
    print(f"已抽取 JD 总数：{len(rows)}", flush=True)

    now = datetime.now(timezone(timedelta(hours=8))).isoformat()
    agg = build_aggregates(rows)
    print(f"聚合岗位族数：{len(agg)}", flush=True)

    with_ts = sum(1 for pa in agg.values() if pa.typical_scenarios)
    print(f"含典型场景的岗位族：{with_ts}", flush=True)

    def _write():
        with neo4j_driver.session() as session:
            return write_aggregates(session, agg, now)

    result = await asyncio.to_thread(_write)
    print(f"聚合写回完成：{result}", flush=True)


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    main()
