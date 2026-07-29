"""JobSpy 采集脚本（LinkedIn，独立运行避免与 Scrapy Twisted 事件循环冲突）。

被 spiders/linkedin_public.py 通过 subprocess 调用，输出 JSONL 到 stdout。
状态/错误日志输出到 stderr。

参考项目：https://github.com/speedyapply/JobSpy
"""

import argparse
import json
import os
import sys
import traceback


def log(msg: str):
    """日志输出到 stderr，不污染 stdout 的 JSONL。"""
    print(msg, file=sys.stderr, flush=True)


def crawl(keyword: str, city: str, results_wanted: int = 20) -> int:
    """调用 JobSpy 采集 LinkedIn 岗位，输出 JSONL 到 stdout。"""
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log("❌ jobspy 未安装，请运行: pip install python-jobspy")
        return 1

    try:
        result = scrape_jobs(
            site_name=["linkedin"],
            search_term=keyword,
            location=city,
            results_wanted=results_wanted,
            country_indeed="USA",
            hours_old=72,  # 最近 3 天
        )
    except Exception as e:
        log(f"❌ JobSpy 采集失败: {type(e).__name__}: {e}")
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

    log(f"✅ 采集完成: kw={keyword} city={city} count={count}")
    return 0 if count > 0 else 3


def main():
    parser = argparse.ArgumentParser(description="JobSpy LinkedIn 采集脚本")
    parser.add_argument("--keyword", required=True, help="搜索关键词")
    parser.add_argument("--city", required=True, help="城市")
    parser.add_argument("--results-wanted", type=int, default=20, help="采集数量")
    args = parser.parse_args()

    sys.exit(crawl(args.keyword, args.city, args.results_wanted))


if __name__ == "__main__":
    main()
