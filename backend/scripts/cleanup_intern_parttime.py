"""清理 jd_raw 中实习/兼职岗位的存量数据，并同步清理图谱证据。

背景：2026-08-05 起爬虫管道（crawlers/pipelines.py CleaningPipeline）在源头
过滤实习/兼职岗位，不再落 jd_raw；本脚本清理此前积累的存量记录。

清理范围（与 pipelines._employment_reason 判定逻辑一致）：
1. jd_raw：删除 实习/兼职 岗位行
2. Neo4j Evidence：删除这些 JD 对应的证据节点（含挂载在正式岗位下的）
3. 岗位聚合：从剩余 jd_raw 重算 Position.freq / REQUIRES weight/source_count

使用（容器内）：
    docker cp scripts/cleanup_intern_parttime.py zhigang-api:/app/cleanup_intern_parttime.py
    docker exec zhigang-api python /app/cleanup_intern_parttime.py --dry-run   # 仅报告
    docker exec zhigang-api python /app/cleanup_intern_parttime.py             # 执行
"""

import argparse
import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import delete, select  # noqa: E402

from app.core.database import async_session_factory, neo4j_driver  # noqa: E402
from app.models.raw import JDRaw  # noqa: E402
from app.services.kg.aggregation import build_aggregates, write_aggregates  # noqa: E402

# ── 实习/兼职判定：与 crawlers/pipelines.py CleaningPipeline 源头过滤保持一致 ──
# 容器镜像未携带 crawlers 包，此处内联相同逻辑；修改 pipelines.py 时需同步本脚本。
_INTERN_RE = re.compile(r"\bintern(?:ship)?s?\b", re.IGNORECASE)
_PARTTIME_RE = re.compile(r"\bpart[\s_\-]?time\b", re.IGNORECASE)
_INTERN_CN = "实习"
_PARTTIME_CN = "兼职"


def _employment_reason(item: dict) -> str | None:
    title = str(item.get("title") or "")
    tags_text = " ".join(
        str(t) for t in (item.get("tags") or []) if isinstance(t, str)
    )
    if _INTERN_CN in title or _INTERN_RE.search(title):
        return "实习岗位"
    if _PARTTIME_CN in title or _PARTTIME_RE.search(title):
        return "兼职岗位"
    # 英文 job_type 标签（中文标签为技能/招聘对象描述，不以此判定）
    if _INTERN_RE.search(tags_text):
        return "实习岗位"
    if _PARTTIME_RE.search(tags_text):
        return "兼职岗位"
    return None


async def _load_jd_rows():
    async with async_session_factory() as s:
        return (await s.scalars(select(JDRaw))).all()


def _delete_evidence(urls: list[str]) -> int:
    """删除指定 source_url 的 Evidence 节点（DETACH 连带删除 HAS_EVIDENCE/EVIDENCED_BY 边）。"""
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
    """从剩余 jd_raw 重算岗位聚合（复用 app.services.kg.aggregation 口径）。"""
    rows = await _load_jd_rows()
    agg = build_aggregates(rows)
    now = datetime.now(timezone.utc).isoformat()
    with neo4j_driver.session() as session:
        return write_aggregates(session, agg, now)


async def main() -> None:
    parser = argparse.ArgumentParser(description="清理实习/兼职岗位存量数据（jd_raw + 图谱证据）")
    parser.add_argument("--dry-run", action="store_true", help="仅报告，不删除")
    args = parser.parse_args()

    rows = await _load_jd_rows()

    # 1. 筛选待清理的 JD
    targets = [
        (r.id, r.source, (r.snapshot or {}).get("title", ""), _employment_reason(r.snapshot or {}), r.source_url)
        for r in rows
        if _employment_reason(r.snapshot or {})
    ]
    by_reason: dict[str, int] = {}
    for _, _, _, reason, _ in targets:
        by_reason[reason] = by_reason.get(reason, 0) + 1
    print(f"jd_raw 总数: {len(rows)}  待清理: {len(targets)} 条（{by_reason}）")

    urls = sorted({u for _, _, _, _, u in targets if u})
    print(f"涉及 source_url: {len(urls)} 个")

    with neo4j_driver.session() as session:
        ev_count = session.run(
            "MATCH (e:Evidence) WHERE e.source_url IN $urls RETURN count(e) AS c", urls=urls
        ).single()["c"]
    print(f"图谱将删除 Evidence 节点: {ev_count} 个")

    if args.dry_run:
        print("\n[dry-run] 未执行任何删除")
        return

    # 2. 删除图谱 Evidence（先删，保留 jd_raw 期间可追溯 source_url）
    deleted = _delete_evidence(urls)
    print(f"[1/3] 已删除图谱 Evidence: {deleted} 个")

    # 3. 删除 jd_raw 行
    ids = [t[0] for t in targets if t[0] is not None]
    await _delete_jd_rows(ids)
    print(f"[2/3] 已删除 jd_raw: {len(ids)} 条")

    # 4. 重聚合岗位（freq / REQUIRES 重算）
    result = await _reaggregate()
    print(f"[3/3] 岗位聚合重算完成: {result}")

    # 5. 终态
    rows_after = await _load_jd_rows()
    with neo4j_driver.session() as session:
        ev_after = session.run("MATCH (e:Evidence) RETURN count(e) AS c").single()["c"]
    print(f"清理后 jd_raw: {len(rows_after)} 条  Evidence: {ev_after} 个")


if __name__ == "__main__":
    asyncio.run(main())
