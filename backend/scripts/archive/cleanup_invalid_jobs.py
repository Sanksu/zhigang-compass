"""清理 jd_raw 非正常岗位存量（批量聚合帖 + 特定污染岗位），并同步清理图谱证据。

清理范围：
1. 批量聚合帖/话术帖：标题无岗位名（纯技术栈列表/招聘话术），判定复用
   crawlers.pipelines._invalid_job_reason（与爬虫管道源头过滤同口径）
2. 特定污染岗位：归一化后命中黑名单（如"系统""解决方案""DevOps 工具"等
   泛词/碎片岗位名）的记录——这些岗位名不是单一正常岗位

动作：删除 jd_raw 行 + 图谱对应 Evidence + 重算聚合（幂等，--dry-run 预览）。

使用：
    uv run python scripts/cleanup_invalid_jobs.py --dry-run   # 仅报告
    uv run python scripts/cleanup_invalid_jobs.py             # 执行
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_BACKEND_DIR / "data"))  # crawlers 包

from app.core.logging import setup_logging

logger = setup_logging("cleanup_invalid_jobs")

from sqlalchemy import delete, select  # noqa: E402

from app.core.database import async_session_factory, neo4j_driver  # noqa: E402
from app.models.raw import JDRaw  # noqa: E402
from app.services.kg.aggregation import build_aggregates, write_aggregates  # noqa: E402
from crawlers.pipelines import _invalid_job_reason  # noqa: E402

# 归一化后命中的污染岗位名黑名单（用户确认：泛词/碎片岗位名，非正常单一岗位）
_INVALID_POSITION_NAMES = {
    "解决方案", "DevOps 工具", "站点可靠性", "系统",
}


def _is_invalid_position(snapshot: dict) -> str | None:
    """特定污染岗位名命中时返回原因；否则 None。"""
    from app.services.extraction.dictionary import normalize_position_name

    ext = snapshot.get("extraction") or {}
    pos = normalize_position_name((ext.get("position_name") or "").strip())
    if pos in _INVALID_POSITION_NAMES:
        return f"污染岗位名: {pos}"
    return None


async def _load_all() -> list[JDRaw]:
    async with async_session_factory() as s:
        return list((await s.scalars(select(JDRaw))).all())


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
    async with async_session_factory() as s:
        rows = (await s.scalars(select(JDRaw))).all()
    agg = build_aggregates(rows)
    now = datetime.now(timezone.utc).isoformat()
    with neo4j_driver.session() as session:
        return write_aggregates(session, agg, now)


async def main() -> None:
    parser = argparse.ArgumentParser(description="清理非正常岗位（jd_raw + 图谱证据 + 重聚合）")
    parser.add_argument("--dry-run", action="store_true", help="仅报告，不删除")
    args = parser.parse_args()

    rows = await _load_all()
    targets: list[JDRaw] = []
    by_reason: dict[str, int] = {}
    for r in rows:
        snap = r.snapshot or {}
        reason = _invalid_job_reason(snap) or _is_invalid_position(snap)
        if reason:
            targets.append(r)
            by_reason[reason] = by_reason.get(reason, 0) + 1

    urls = sorted({r.source_url for r in targets if r.source_url})
    logger.info("jd_raw 总数: %s  待清理: %s 条（%s）", len(rows), len(targets), by_reason)
    logger.info("涉及 source_url: %s 个", len(urls))

    with neo4j_driver.session() as session:
        ev_count = session.run(
            "MATCH (e:Evidence) WHERE e.source_url IN $urls RETURN count(e) AS c", urls=urls
        ).single()["c"]
    logger.info("图谱将删除 Evidence: %s 个", ev_count)

    # 抽样展示待清理记录（供误杀核对）
    for r in targets[:15]:
        logger.info("  [%s] id=%s title=%r", r.source, r.id, r.snapshot.get("title", ""))

    if args.dry_run:
        logger.info("[dry-run] 未执行任何删除")
        return

    # 删除前导出待删清单备份（08-15 中危修复：删除不可逆——备份供误杀恢复/审计）
    backup_dir = _BACKEND_DIR / "reports"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"cleanup_invalid_jobs_deleted_{datetime.now().strftime('%Y%m%d_%H%M')}.jsonl"
    with backup_path.open("w", encoding="utf-8") as fh:
        for r in targets:
            fh.write(json.dumps({
                "id": r.id, "source": r.source, "source_id": r.source_id,
                "source_url": r.source_url, "title": (r.snapshot or {}).get("title", ""),
                "reason": _invalid_job_reason(r.snapshot or {}) or _is_invalid_position(r.snapshot or {}),
            }, ensure_ascii=False) + "\n")
    logger.info("待删清单已备份: %s（%s 条）", backup_path, len(targets))

    deleted = _delete_evidence(urls)
    logger.info("[1/3] 已删除图谱 Evidence: %s 个", deleted)

    ids = [r.id for r in targets]
    await _delete_jd_rows(ids)
    logger.info("[2/3] 已删除 jd_raw: %s 条", len(ids))

    result = await _reaggregate()
    logger.info("[3/3] 岗位聚合重算完成: %s", result)


if __name__ == "__main__":
    asyncio.run(main())
