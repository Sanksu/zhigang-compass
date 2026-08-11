"""多平台数据交叉验证脚本（DA-M3-03，设计文档 §4.5）。

清洗管线末端执行：聚合 jd_raw 已抽取记录 → 按归一化岗位名分组 →
跨平台校验（技能一致性/薪资异常/经验分歧/跨源置信度）→
输出 `reports/validation_report_{date}.json` 并写回 `snapshot["cross_validation"]`。

用法：
    python scripts/cross_validate.py            # 全量 717 条
    python scripts/cross_validate.py --limit 100   # 仅前 N 条（冒烟）
    python scripts/cross_validate.py --no-write    # 不写回 snapshot
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("cross_validate")

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.raw import JDRaw
from app.services.data_quality.cross_validate import build_position_groups, validate_group

_REPORT_DIR = _BACKEND_DIR / "reports"


def _group_summary(groups: dict[str, list[dict]], limit: int | None) -> list[dict]:
    """校验全部岗位组，返回结果列表（含全局统计）。"""
    results = []
    for pos, group in groups.items():
        results.append(validate_group(pos, group))
    results.sort(key=lambda r: r.source_count, reverse=True)
    return results[:limit] if limit else results


def _print_report(results: list[dict], stats: dict) -> None:
    logger.info("=" * 60)
    logger.info("多平台数据交叉验证报告（DA-M3-03）")
    logger.info("=" * 60)
    logger.info(f"岗位组总数: {stats['total_groups']}（覆盖 JD {stats['jd_count']} 条）")
    logger.info(f"跨源组: {stats['multi_source_groups']} | 单源组: {stats['single_source_groups']}")
    logger.info(f"验证通过（≥2 源印证）: {stats['verified_groups']}")
    logger.info(f"薪资异常组: {stats['salary_outlier_groups']}")
    logger.info(f"置信度 < 0.6（不入图谱候选）: {stats['below_confidence']}")
    logger.info(f"单源技能总数（待人工审核）: {stats['unverified_skills']}")
    logger.info("\nTop 跨源岗位组:")
    for r in results[:10]:
        logger.info(
            f"  {r['position_name']:<28} 源={r['source_count']} JD={r['jd_count']} "
            f"verified={'Y' if r['verified'] else 'N'} conf={r['confidence']} "
            f"薪资={r['salary_median']} 异常={'Y' if r['salary_outlier'] else 'N'}"
        )


async def main(limit: int | None, write_back: bool) -> None:
    async with async_session_factory() as session:
        stmt = select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        rows = (await session.scalars(stmt.order_by(JDRaw.id.asc()))).all()
        if limit:
            rows = rows[:limit]

        records = [
            {"snapshot": r.snapshot or {}, "source": r.source, "crawled_at": r.crawled_at}
            for r in rows
        ]
        groups = build_position_groups(records)
        results = _group_summary(groups, None)

        stats = {
            "total_groups": len(results),
            "jd_count": len(records),
            "multi_source_groups": sum(1 for r in results if r.source_count >= 2),
            "single_source_groups": sum(1 for r in results if r.source_count == 1),
            "verified_groups": sum(1 for r in results if r.verified),
            "salary_outlier_groups": sum(1 for r in results if r.salary_outlier),
            "below_confidence": sum(1 for r in results if r.confidence < 0.6),
            "unverified_skills": sum(len(r.unverified_skills) for r in results),
        }
        _print_report([r.model_dump() for r in results], stats)

        # 写回 snapshot["cross_validation"]（按 JD 归属组）
        if write_back:
            group_map = {r.position_name: r for r in results}
            written = 0
            for row in rows:
                ext = (row.snapshot or {}).get("extraction") or {}
                from app.services.extraction.dictionary import normalize_position_name

                pos = normalize_position_name(
                    ext.get("position_name") or "",
                    skills=[s.get("name", "") for s in (ext.get("skills") or []) if s.get("name")],
                )
                result = group_map.get(pos)
                if result is None:
                    continue
                snap = dict(row.snapshot or {})
                snap["cross_validation"] = result.model_dump()
                row.snapshot = snap
                written += 1
            await session.commit()
            logger.info("已写回 snapshot['cross_validation']: %s 条", written)

        _REPORT_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")
        report_path = _REPORT_DIR / f"validation_report_{date_str}.json"
        report_path.write_text(
            json.dumps(
                {"run_date": date_str, "stats": stats, "groups": [r.model_dump() for r in results]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info(f"报告输出: {report_path.relative_to(_BACKEND_DIR)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="多平台数据交叉验证")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 条（冒烟）")
    parser.add_argument("--no-write", action="store_true", help="不写回 snapshot")
    args = parser.parse_args()
    asyncio.run(main(args.limit, write_back=not args.no_write))
