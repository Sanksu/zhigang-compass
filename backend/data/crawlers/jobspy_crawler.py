"""JobSpy 采集脚本（独立运行，避免与 Scrapy Twisted 事件循环冲突）。

被 spiders/indeed.py、spiders/linkedin_public.py 通过 subprocess 调用，
输出 JSONL 到 stdout。状态/错误日志输出到 stderr。

参考项目：https://github.com/speedyapply/JobSpy
"""

import argparse
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.core.logging import setup_logging

logger = setup_logging("jobspy_crawler", stream=sys.stderr)


def crawl(site: str, keyword: str, city: str, results_wanted: int = 20,
          days_old: int = 3) -> int:
    """调用 JobSpy 采集岗位，输出 JSONL 到 stdout。

    days_old: 只采集最近 N 天发布的岗位（历史回爬 G-01 参数化，
        默认 3 天对齐原 hours_old=72 的增量语义）。
        注意：Indeed 平台对发布时间筛选有窗口上限，超大值可能取不满。
    """
    try:
        from jobspy import scrape_jobs
    except ImportError:
        logger.error("❌ jobspy 未安装，请运行: pip install python-jobspy")
        return 1

    try:
        result = scrape_jobs(
            site_name=[site],
            search_term=keyword,
            location=city,
            results_wanted=results_wanted,
            country_indeed="USA",
            hours_old=days_old * 24,
        )
    except Exception as e:
        logger.error(f"❌ JobSpy 采集失败: {type(e).__name__}: {e}")
        traceback.print_exc(file=sys.stderr)
        return 2

    count = 0
    for _, row in result.iterrows():
        # 转为 dict，处理 NaN 和 None（JobSpy 部分字段返回 None 而非 NaN）
        item = {}
        for k in result.columns:
            v = row[k]
            if v is None or (isinstance(v, float) and v != v):  # None 或 NaN
                item[k] = ""
            else:
                item[k] = str(v) if not isinstance(v, (int, float, bool, list, dict)) else v

        print(json.dumps(item, ensure_ascii=False), flush=True)
        count += 1

    logger.info(f"✅ 采集完成: site={site} kw={keyword} city={city} count={count}")
    return 0 if count > 0 else 3


def main():
    parser = argparse.ArgumentParser(description="JobSpy 采集脚本（Indeed/LinkedIn）")
    parser.add_argument("--site", required=True, choices=["indeed", "linkedin"], help="JobSpy site_name")
    parser.add_argument("--keyword", required=True, help="搜索关键词")
    parser.add_argument("--city", required=True, help="城市")
    parser.add_argument("--results-wanted", type=int, default=20, help="采集数量")
    parser.add_argument("--days-old", type=int, default=3,
                        help="只采集最近 N 天发布的岗位（历史回爬 G-01，默认 3）")
    args = parser.parse_args()

    sys.exit(crawl(args.site, args.keyword, args.city,
                   args.results_wanted, args.days_old))


if __name__ == "__main__":
    main()
