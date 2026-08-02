"""本地低频爬虫采集脚本（数据入库，忽略 BOSS 源）。

用途：
    在本地按低频节奏串行运行各数据源爬虫，结果经 PostgresPipeline 入库
    （upsert 幂等，重复运行不产生脏数据）。

低频策略：
    - 源间固定间隔（默认 60s），避免瞬时高频率触发反爬/风控
    - 各源使用小参数（少量关键词/城市/页数），单次采集量小
    - 海外源需代理（默认 http://127.0.0.1:7890）

忽略源：
    - BOSS：依赖登录态与 CDP 浏览器，不在本脚本内运行
    - Monster / Glassdoor：需 CDP 浏览器，默认跳过，--with-cdp 时启用

用法：
    python scripts/run_crawlers_lowfreq.py                 # 默认低频采集
    python scripts/run_crawlers_lowfreq.py --interval 120  # 源间间隔 120s
    python scripts/run_crawlers_lowfreq.py --no-proxy      # 海外源直连（不推荐）
    python scripts/run_crawlers_lowfreq.py --with-cdp      # 启用 Monster/Glassdoor（需先启动 CDP 浏览器）
"""

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
CRAWLERS_DIR = BACKEND_DIR / "data" / "crawlers"
OUTPUT_DIR = CRAWLERS_DIR / "output"
PYTHON = sys.executable

# 单个爬虫任务的最大等待秒数（含 subprocess 内部超时，留足余量）
TASK_TIMEOUT = 1200

# 海外源代理（Clash/V2Ray 默认端口）
DEFAULT_PROXY = "http://127.0.0.1:7890"


@dataclass
class CrawlTask:
    """单个爬虫任务：scrapy crawl 的参数 + 运行条件。"""

    name: str
    args: list[str]
    needs_proxy: bool = False
    needs_cdp: bool = False


# 低频参数：每个源用最小采集量，覆盖国内（直连）+ 海外（代理）数据源
TASKS: list[CrawlTask] = [
    # 国内直连
    CrawlTask("zhilian", ["crawl", "zhilian", "-a", "keywords=Python,Java", "-a", "cities=北京,上海"]),
    CrawlTask("icourse163", ["crawl", "icourse163", "-a", "keywords=机器学习,Python", "-a", "max_pages=1"]),
    # 海外（需代理）
    CrawlTask("github", ["crawl", "github", "-a", "languages=python,typescript", "-a", "since=daily"], needs_proxy=True),
    CrawlTask("arxiv", ["crawl", "arxiv", "-a", "categories=cs.AI,cs.LG", "-a", "max_results=30"], needs_proxy=True),
    CrawlTask("coursera", ["crawl", "coursera", "-a", "keywords=Python,Machine-Learning", "-a", "max_pages=1"], needs_proxy=True),
    CrawlTask("edx", ["crawl", "edx", "-a", "keywords=Python,Data-Science", "-a", "max_pages=1"], needs_proxy=True),
    CrawlTask("indeed", ["crawl", "indeed", "-a", "keywords=Python", "-a", "cities=New York"], needs_proxy=True),
    CrawlTask("linkedin_public", ["crawl", "linkedin_public", "-a", "keywords=Python", "-a", "cities=New York"], needs_proxy=True),
    # Stack Overflow 常被 Cloudflare 拦截，失败不阻塞整体
    CrawlTask("stackoverflow", ["crawl", "stackoverflow", "-a", "tags=python", "-a", "max_pages=1"], needs_proxy=True),
    # CDP 源（默认跳过，--with-cdp 启用）
    CrawlTask("monster", ["crawl", "monster", "-a", "keywords=Python", "-a", "cities=New York"], needs_cdp=True),
    CrawlTask("glassdoor", ["crawl", "glassdoor", "-a", "keywords=Python", "-a", "cities=New York"], needs_cdp=True),
]


def _parse_item_count(stdout: str, stderr: str) -> int | None:
    """从 scrapy 输出提取 "Stored jsonl feed (N items)"。

    scrapy 的统计日志输出到 stderr（而非 stdout），故两者都扫描。
    """
    m = re.search(r"Stored jsonl feed \((\d+) items\)", stdout + "\n" + stderr)
    return int(m.group(1)) if m else None


def run_task(task: CrawlTask, proxy: str | None, with_cdp: bool) -> tuple[str, str]:
    """执行单个爬虫，返回 (状态, 说明)。"""
    if task.needs_cdp and not with_cdp:
        return "SKIP", "需 CDP 浏览器（--with-cdp 启用）"

    env = os.environ.copy()
    # crawlers 包位于 backend/data/ 下（crawlers 是其子包），需将该目录加入 PYTHONPATH
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_DIR), str(CRAWLERS_DIR.parent)])
    if task.needs_proxy and proxy:
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy

    out_file = OUTPUT_DIR / f"lowfreq_{task.name}.jsonl"
    cmd = [PYTHON, "-m", "scrapy", *task.args, "-o", str(out_file), "-s", "ROBOTSTXT_OBEY=False"]

    try:
        proc = subprocess.run(
            cmd, cwd=str(CRAWLERS_DIR), env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=TASK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT", f"超过 {TASK_TIMEOUT}s"

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    count = _parse_item_count(stdout, stderr)
    status = "OK" if proc.returncode == 0 else f"FAIL(rc={proc.returncode})"
    detail = f"产出 {count} 条" if count is not None else stderr.strip().splitlines()[-1][:120] if stderr.strip() else ""
    return status, detail


def main() -> None:
    parser = argparse.ArgumentParser(description="本地低频爬虫采集（忽略 BOSS 源）")
    parser.add_argument("--interval", type=int, default=60, help="源间间隔秒数（默认 60）")
    parser.add_argument("--proxy", default=DEFAULT_PROXY, help=f"海外源代理（默认 {DEFAULT_PROXY}）")
    parser.add_argument("--no-proxy", action="store_true", help="海外源直连（不推荐）")
    parser.add_argument("--with-cdp", action="store_true", help="启用 Monster/Glassdoor（需先启动 CDP 浏览器）")
    args = parser.parse_args()

    proxy = None if args.no_proxy else args.proxy
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"低频爬虫采集开始（间隔 {args.interval}s，代理: {proxy or '无'}，"
          f"CDP 源: {'启用' if args.with_cdp else '跳过'}）")
    print(f"目标源：{', '.join(t.name for t in TASKS)}（忽略 BOSS）\n")

    results: list[tuple[str, str, str]] = []
    for idx, task in enumerate(TASKS, 1):
        print(f"[{idx}/{len(TASKS)}] 开始 {task.name} ...", flush=True)
        t0 = time.time()
        status, detail = run_task(task, proxy, args.with_cdp)
        elapsed = time.time() - t0
        print(f"  -> {task.name}: {status}（{elapsed:.0f}s）{detail}", flush=True)
        results.append((task.name, status, detail))

        # 源间低频间隔（最后一个任务后不再等待）
        if idx < len(TASKS):
            print(f"  等待 {args.interval}s 后继续 ...", flush=True)
            time.sleep(args.interval)

    print("\n========== 采集汇总 ==========")
    ok = sum(1 for _, s, _ in results if s == "OK")
    for name, status, detail in results:
        print(f"  {name:<18} {status:<10} {detail}")
    print(f"\n完成：{ok}/{len(results)} 个源成功。数据已入库（upsert 幂等）。")


if __name__ == "__main__":
    main()
