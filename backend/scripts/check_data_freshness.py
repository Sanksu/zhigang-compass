"""数据更新新鲜度检查脚本（DA-M4-03，设计文档 T+1 承诺）。

按来源聚合四类 raw 表最新抓取时间，判定平台级新鲜度（≤1 天），
输出 reports/freshness_{date}.json + 控制台摘要。

用途：
- 日常巡检：`python scripts/check_data_freshness.py`
- cron 告警：脚本发现过期平台时退出码非 0，可据此触发告警
- 数据更新机制审计：验证每日 ETL 的 T+1 发布承诺是否兑现

用法：
    uv run python scripts/check_data_freshness.py
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select

from app.core.logging import setup_logging

logger = setup_logging("check_data_freshness")

from app.core.database import async_session_factory
from app.models.raw import CommunityRaw, CourseRaw, JDRaw, PaperRaw
from app.services.data_quality.update_status import platform_freshness

_TZ_CN = timezone(timedelta(hours=8))


async def collect() -> dict:
    async with async_session_factory() as s:
        jd_rows = (await s.scalars(select(JDRaw))).all()
        course_rows = (await s.scalars(select(CourseRaw))).all()
        paper_rows = (await s.scalars(select(PaperRaw))).all()
        community_rows = (await s.scalars(select(CommunityRaw))).all()

    return {
        "generated_at": datetime.now(_TZ_CN).isoformat(),
        "jd": platform_freshness([{"source": r.source, "crawled_at": r.crawled_at} for r in jd_rows]),
        "course": platform_freshness([{"source": r.source, "crawled_at": r.crawled_at} for r in course_rows]),
        "paper": platform_freshness([{"source": r.source, "crawled_at": r.crawled_at} for r in paper_rows]),
        "community": platform_freshness([{"source": r.source, "crawled_at": r.crawled_at} for r in community_rows]),
    }


def _print_section(name: str, section: dict) -> None:
    logger.info(f"[{name}] T+1 合规: {'✅' if section['t1_compliant'] else '⚠️ 有过期来源'}")
    for p in section["platforms"]:
        mark = "✅" if p["fresh"] else "⚠️"
        crawl = p["last_crawl"] or "无法解析"
        days = f"{p['days_since']} 天" if p["days_since"] is not None else "?"
        logger.info(f"  {mark} {p['source']:<14} 最新抓取 {crawl} 距今 {days}")


def main() -> int:
    report = asyncio.run(collect())

    logger.info("=" * 56)
    logger.info("智岗罗盘 — 数据更新新鲜度检查（DA-M4-03，T+1 承诺）")
    logger.info("=" * 56)
    for name in ("jd", "course", "paper", "community"):
        _print_section(name, report[name])

    report_dir = _BACKEND_DIR / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"freshness_{datetime.now(_TZ_CN).strftime('%Y%m%d')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"报告已写入: {path.relative_to(_BACKEND_DIR)}")

    all_compliant = all(report[name]["t1_compliant"] for name in ("jd", "course", "paper", "community"))
    if not all_compliant:
        logger.warning("[WARN] 存在过期数据来源（>1 天未更新），请检查 ETL 调度")
        return 1
    logger.info("[OK] 全部来源在 T+1 窗口内更新")
    return 0


if __name__ == "__main__":
    sys.exit(main())
