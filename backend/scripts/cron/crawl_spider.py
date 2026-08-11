"""单个爬虫调度入口（设计文档 §4.4）。

被系统 cron 按各平台时段调用，将 crawl_platform 任务入队到 ARQ。

调用方式：
    # Linux cron
    0 2 * * * cd /path/to/backend && uv run python scripts/cron/crawl_spider.py boss

    # Windows 计划任务
    cd backend; uv run python scripts/cron/crawl_spider.py boss

平台调度时段（设计文档 §4.4，均为北京时间；实际调度以 crontab.example / scheduled_tasks.ps1 为准）：
    02:00  boss / 02:15 zhilian（国内 A 级）
    04:20  indeed / 04:40 glassdoor（国际错峰；monster 因 DataDome 防护不可绕过已停采）
    23:00  maimai（脉脉夜间合规窗口 ≤100 req/h）
    08:00  linkedin_public / github / stackoverflow（国际非招聘源，= UTC 0:00）
    11:00  arxiv（= UTC 3:00）
    周日 10:00  coursera / edx / icourse163（课程平台全量同步，= UTC 2:00）
"""

import asyncio
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("cron.crawl_spider")


async def enqueue_crawl(spider_name: str, max_results: int | None = None) -> None:
    """将单个爬虫任务入队到 ARQ。"""
    from arq import create_pool
    from arq.connections import RedisSettings
    import os
    from urllib.parse import urlparse

    redis_url = os.environ.get("ARQ_REDIS_URL", "redis://localhost:6379/1")
    parsed = urlparse(redis_url)
    redis_settings = RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or "1"),
        password=parsed.password,
    )

    client = await create_pool(redis_settings)
    try:
        kwargs = {"spider_name": spider_name}
        if max_results:
            kwargs["max_results"] = max_results
        job = await client.enqueue_job("crawl_platform", **kwargs)
        logger.info(f"[crawl_spider] 已入队 crawl_platform spider={spider_name} job_id={job.job_id}")
    finally:
        await client.close()


def main() -> int:
    if len(sys.argv) < 2:
        logger.error("用法: python crawl_spider.py <spider_name> [max_results]")
        logger.error("可用 spider: boss / zhilian / monster / indeed / glassdoor / "
                     "maimai / linkedin_public / arxiv / github / stackoverflow / "
                     "coursera / edx / icourse163")
        return 2

    spider_name = sys.argv[1]
    max_results = None
    if len(sys.argv) > 2:
        try:
            max_results = int(sys.argv[2])
        except ValueError:
            logger.error("[crawl_spider] max_results 参数非数字: %s", sys.argv[2])
            return 2

    try:
        asyncio.run(enqueue_crawl(spider_name, max_results))
        return 0
    except Exception as e:
        logger.exception(f"[crawl_spider] 调度失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
