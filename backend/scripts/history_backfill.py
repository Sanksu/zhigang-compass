"""多平台历史回爬脚本（设计文档 §4.2 / 功能缺失追踪 G-01）。

背景：采集首日仅增量爬取（BOSS/智联 max_pages=5），M3 新岗位发现所需的
12 周 JD 历史基线缺失。本脚本一次性回爬近 N 天历史岗位，为观察池
JD 3 月 MA 环比分析提供数据支撑。

- 逐平台串行调用 `scrapy crawl`，带 -a history_days=N，由 spider 层按发布
  时间截断：
  - boss：HTTP 搜索 API，--since-days 按 lastModifyTime 截断（CDP 登录态）
  - zhilian：Playwright SSR，按 publishTime 截断
  - indeed：JobSpy --days-old → hours_old（需 HTTPS_PROXY 代理）
  - glassdoor：CDP 翻页放宽（需 9224 Chrome + Cloudflare 验证，走代理）
- 入库由 Scrapy CleaningPipeline + PostgresPipeline 完成：SHA256 指纹 upsert，
  重爬天然幂等，不触发重复 LLM 抽取（snapshot JSONB 合并保留 extraction）
- 单源失败按指数退避重试（30s→60s→120s→300s，与 crawlers.middlewares
  backoff_delay 同语义），连续 MAX_ATTEMPTS 次失败停止该源并 webhook 告警
- 产出统计写入 reports/history_backfill_{YYYYMMDD}.json

用法：
    uv run python scripts/history_backfill.py                  # 默认全部平台 90 天
    uv run python scripts/history_backfill.py --days 60 --platforms boss,indeed
    uv run python scripts/history_backfill.py --dry-run        # 只打印命令不执行
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_BACKEND_DIR / "data"))  # crawlers 包

from app.core.logging import setup_logging

logger = setup_logging("history_backfill")

from crawlers.middlewares import backoff_delay  # noqa: E402
from app.services.alerting import send_alert  # noqa: E402

# 回爬平台：国内 A 级（boss/zhilian）+ 国际源（indeed/glassdoor）。
# linkedin 未在生产启用、monster 已停采（DataDome 防护），不列入。
DEFAULT_PLATFORMS = ["boss", "zhilian", "indeed", "glassdoor"]
DEFAULT_DAYS = 90
# 单源最大尝试次数（含首次）；退避序列 30/60，第 3 次失败即放弃
MAX_ATTEMPTS = 3
# scrapy 子进程超时（对齐 spiders 侧 SUBPROCESS_TIMEOUT=300s）
SUBPROCESS_TIMEOUT = 300

# 爬虫项目根（backend/data/crawlers，scrapy.cfg 同目录）
_CRAWLERS_DIR = _BACKEND_DIR / "data" / "crawlers"
_OUTPUT_DIR = _CRAWLERS_DIR / "output"
_REPORTS_DIR = _BACKEND_DIR / "reports"

_UTF8_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
_CRAWL_ENV = {
    **_UTF8_ENV,
    "PYTHONPATH": os.pathsep.join(
        [str(_CRAWLERS_DIR.parent), str(_CRAWLERS_DIR.parent.parent)]
    ),
}


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def build_cmd(platform: str, days: int, max_pages: int, output_file: Path) -> list[str]:
    """构造 scrapy 回爬命令。days=0 时不传 history_days（增量采集）。"""
    cmd = [
        sys.executable, "-m", "scrapy", "crawl", platform,
        "-o", str(output_file),
    ]
    if days:
        cmd.extend(["-a", f"history_days={days}"])
    cmd.extend(["-a", f"max_pages={max_pages}"])
    return cmd


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as f:
        return sum(1 for _ in f)


async def run_scrapy(cmd: list[str], output_file: Path) -> dict:
    """执行单次 scrapy，返回 {returncode, items, ok, error}。

    对齐 crawl_platform 的成功判定：退出码 0 且产出 > 0。
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(_CRAWLERS_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_CRAWL_ENV,
    )
    # 超时保护：scrapy 子进程可能因网络/风控卡死，无限等待会拖停整个多平台串行回爬
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=SUBPROCESS_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {
            "returncode": -1, "items": _line_count(output_file), "ok": False,
            "error": f"子进程超时（>{SUBPROCESS_TIMEOUT}s），已终止",
        }
    stderr_tail = stderr.decode("utf-8", errors="replace")[-2000:]
    items = _line_count(output_file)

    if proc.returncode != 0:
        return {
            "returncode": proc.returncode, "items": items, "ok": False,
            "error": f"退出码 {proc.returncode}: {stderr_tail}",
        }
    if items == 0:
        return {
            "returncode": 0, "items": 0, "ok": False,
            "error": f"产出 0 条: {stderr_tail}",
        }
    return {"returncode": 0, "items": items, "ok": True, "error": ""}


async def _backfill_platform(platform: str, days: int, max_pages: int,
                             out_dir: Path) -> dict:
    """回爬单个平台，失败退避重试，最终失败发 webhook 告警。"""
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    output_file = out_dir / f"{platform}_backfill_{timestamp}.jsonl"
    cmd = build_cmd(platform, days, max_pages, output_file)
    logger.info(f"[history_backfill] {platform}: {' '.join(cmd)}")

    result = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = await run_scrapy(cmd, output_file)
        if result["ok"]:
            return {
                "status": "success", "attempts": attempt,
                "items": result["items"],
                "output_file": str(output_file),
            }
        logger.error(f"[history_backfill] {platform} 第 {attempt} 次失败: {result['error']}")
        if attempt < MAX_ATTEMPTS:
            delay = backoff_delay(attempt - 1)
            logger.info(f"[history_backfill] {platform} 退避 {delay}s 后重试")
            await asyncio.sleep(delay)

    # 连续失败：发告警，不再阻塞其他平台
    await send_alert(
        "crawl_failed",
        f"A 级平台历史回爬失败：{platform}（连续 {MAX_ATTEMPTS} 次，"
        f"最近错误：{result['error'][:300]}）",
        platform=platform, days=days, run_date=_today(),
    )
    return {"status": "failed", "attempts": MAX_ATTEMPTS, "error": result["error"]}


def _write_report(report: dict, reports_dir: Path) -> Path:
    """写统计报告到 reports/，返回路径。"""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"history_backfill_{report['run_date'].replace('-', '')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def run_backfill(platforms: list[str], days: int, max_pages: int,
                       out_dir: Path, dry_run: bool = False) -> dict:
    """逐平台串行回爬。返回 {run_date, days, platforms: {...}}。"""
    results: dict = {}
    for platform in platforms:
        output_file = out_dir / f"{platform}_backfill_dryrun.jsonl"
        cmd = build_cmd(platform, days, max_pages, output_file)
        if dry_run:
            logger.info(f"[history_backfill][dry-run] {' '.join(cmd)}")
            results[platform] = {"status": "dry-run", "command": cmd}
            continue
        results[platform] = await _backfill_platform(platform, days, max_pages, out_dir)

    report = {"run_date": _today(), "days": days, "platforms": results}
    if not dry_run:
        report_path = _write_report(report, _REPORTS_DIR)
        logger.info(f"[history_backfill] 统计报告: {report_path}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="A 级平台 90 天历史回爬")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--platforms", default=",".join(DEFAULT_PLATFORMS))
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        report = asyncio.run(run_backfill(
            platforms, args.days, args.max_pages, _OUTPUT_DIR, args.dry_run,
        ))
    except Exception as e:
        logger.error(f"[history_backfill] 执行失败: {e}")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
