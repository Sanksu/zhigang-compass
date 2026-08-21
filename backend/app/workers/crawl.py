"""Scrapy subprocess worker implementation and crawl configuration."""

import asyncio
import logging
import os
import platform
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core import runtime_config
from app.services.alerting import send_alert
from app.workers.utils import push_crawl_log, update_crawl_task

logger = logging.getLogger(__name__)

# 子进程 stdout/stderr 强制 UTF-8（中文 Windows 默认 GBK 管道，按 UTF-8 解码会乱码）
# 与 crawlers/spiders 下各 spider 调外部进程的模式一致
_UTF8_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

# ── 爬虫项目根（backend/data/crawlers）──
_CRAWLERS_DIR = Path(__file__).resolve().parents[2] / "data" / "crawlers"
_OUTPUT_DIR = _CRAWLERS_DIR / "output"

# 爬虫子进程需 import crawlers 包（位于 backend/data/，scrapy.cfg 同目录），
# 显式设置 PYTHONPATH——worker 以服务方式启动时继承不到交互终端的 PYTHONPATH，
# 缺省会导致 `ModuleNotFoundError: No module named 'crawlers'`，全部爬虫静默失败
_CRAWL_ENV = {
    **_UTF8_ENV,
    "PYTHONPATH": os.pathsep.join(
        [str(_CRAWLERS_DIR.parent), str(_CRAWLERS_DIR.parent.parent)]
    ),
}

# 显式消费 -a max_results 参数的 spider（其余源由各自默认采集量控制）
# zhilian：08-13 新增条数上限（默认 200，spider 层 CloseSpider 截断）
MAX_RESULTS_SUPPORTED = {"arxiv", "zhilian"}

# CDP 爬虫：需连接真实 Chrome（9222）复用登录态，无登录态时会自动拉起浏览器
# （ensure_cdp_chrome），本地手动触发 ETL 可 skip_cdp=True 跳过，避免干扰用户浏览器
CDP_SPIDERS = {"boss", "monster", "glassdoor", "maimai"}

# 单源爬虫超时上限（秒）：Playwright 渲染慢源（zhilian 8kw×5city 全量）正常
# 需 20-40min，但挂死（网络黑洞/风控验证码循环）会无限阻塞 ETL 阶段 1
# （08-13 实测 zhilian 挂死 8h，job 超时 kill 后 subprocess 成孤儿继续跑）。
# 900s 对齐 run_etl_pipeline 注释声明的单源上限；超时 kill 后已写入 jsonl
# 保留（Scrapy 逐行落盘），后续 load 仍消费已产出数据。
# 08-14 审查：按源分级——zhilian 全量正常 20-40min，900s 恒杀正常采集；
# 慢渲染源单独放宽（2400s = 40min 上限），其余源维持 15min 兜底
_CRAWL_TIMEOUT_SEC = 900
# 单源超时上限（秒）。zhilian 详情补抓 8-15s/条限速，max_results=200 有界
# 正常耗时约 1.6h（5760s）——超时须 > 正常耗时（防误杀），仍兜底挂死。
_CRAWL_TIMEOUT_BY_SPIDER = {"zhilian": 7200}


def _crawl_timeout(spider_name: str) -> int:
    """按源取超时上限（zhilian 40min，其余 15min）。"""
    return _CRAWL_TIMEOUT_BY_SPIDER.get(spider_name, _CRAWL_TIMEOUT_SEC)


def _kill_process_tree(proc) -> None:
    """终止爬虫子进程树（08-14 修复：proc.kill() 只杀主进程，Playwright/Chrome
    子进程成孤儿继续打源站——08-13 zhilian 实测挂死 8h）。

    Windows：taskkill /T /F（进程树）；POSIX：killpg（创建时 start_new_session
    进程组隔离）。
    """
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
            if result.returncode != 0:
                proc.kill()  # taskkill 失败（pid 无效等）兜底杀主进程
        except Exception:
            proc.kill()
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


async def crawl_platform(
    ctx: dict,
    spider_name: str,
    keywords: list[str] | None = None,
    cities: list[str] | None = None,
    max_results: int | None = None,
    task_id: str | None = None,
) -> dict:
    """触发单个 Scrapy 爬虫。

    通过 subprocess 调用而非 in-process，原因：
    - Scrapy 基于 Twisted reactor，与 asyncio event loop 不兼容
    - subprocess 隔离崩溃，单爬虫失败不污染 worker

    单源超时：_CRAWL_TIMEOUT_SEC（900s）内未退出则 kill 子进程并报错——
    避免爬虫挂死无限阻塞 ETL 阶段 1（ARQ job 超时不会终止 subprocess，
    会残留孤儿爬虫继续打源站）。

    task_id 存在时（手动触发场景）：
    - 输出逐行写入 Redis 日志队列（SSE 端点 /admin/crawl/task/{task_id}/stream 实时推送）
    - 同步 TaskStatus 状态（running → success/failed），进度 0.1 → 1.0

    输出：output/{spider}_{YYYYMMDD_HHMMSS}.jsonl
    """
    timestamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
    output_file = _OUTPUT_DIR / f"{spider_name}_{timestamp}.jsonl"
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(
        "任务开始: task_id=%s spider=%s keywords=%s cities=%s output=%s",
        task_id, spider_name, keywords, cities or "(默认)", output_file,
    )

    cmd = [
        sys.executable, "-m", "scrapy", "crawl", spider_name,
        "-o", str(output_file),
    ]
    if keywords:
        cmd.extend(["-a", f"keywords={','.join(keywords)}"])
    if cities:
        cmd.extend(["-a", f"cities={','.join(cities)}"])
    # max_results 仅 arxiv 等显式消费该参数的 spider 生效，其余忽略并提示（避免静默失效）
    if max_results:
        if spider_name in MAX_RESULTS_SUPPORTED:
            cmd.extend(["-a", f"max_results={max_results}"])
        else:
            logger.warning("spider=%s 不支持 max_results，参数已忽略", spider_name)
    logger.info("完整命令: %s", " ".join(cmd))

    await update_crawl_task(
        task_id,
        status="running",
        progress=0.1,
        # L-5：SSE 会把 result 快照全量下发给管理端，与成功路径一致存仓库相对路径，
        # 不暴露服务端绝对路径（resume 域同口径）
        result={
            "spider": spider_name,
            "output_file": str(output_file.relative_to(_CRAWLERS_DIR.parent.parent)),
        },
    )

    # cwd 设到 crawlers/ 让 scrapy.cfg 生效；env 强制 UTF-8 + PYTHONPATH（见 _CRAWL_ENV）
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(_CRAWLERS_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_CRAWL_ENV,
            start_new_session=True,  # POSIX 进程组隔离：超时 killpg 可连带子进程
        )
    except Exception as e:
        msg = f"启动爬虫子进程失败: {e}"
        logger.error("%s", msg)
        await update_crawl_task(task_id, status="failed", error=msg[:500])
        await send_alert("crawl_failed", msg, spider=spider_name)
        raise RuntimeError(msg) from e
    logger.info("子进程已启动: task_id=%s pid=%s", task_id, getattr(proc, "pid", "?"))

    # 并发逐行读取 stdout/stderr：实时写入日志队列，stderr 尾部留存用于失败信息
    stderr_tail: list[str] = []

    async def _drain(stream, tail: list[str] | None = None):
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            await push_crawl_log(ctx, task_id, text)
            if tail is not None:
                tail.append(text)
                if len(tail) > 200:
                    tail.pop(0)

    # 单源超时保护（P0-1）：爬虫挂死时 kill 子进程，避免 ETL 阶段 1 无限阻塞；
    # 已写入 jsonl 保留（Scrapy 逐行落盘），后续 load 消费已产出数据
    # 08-15 审查 H1：drain 必须与 wait_for 同域——原实现 gather 在 wait_for 之前，
    # 子进程挂死且无输出时 readline 永不 EOF，wait_for 永不触发（kill 成摆设）
    timeout = _crawl_timeout(spider_name)
    try:
        await asyncio.wait_for(
            asyncio.gather(
                _drain(proc.stdout),
                _drain(proc.stderr, stderr_tail),
            ),
            timeout=timeout,
        )
        # gather 完成 = stdout/stderr 已 EOF，进程退出在即；wait 仍套 10s 短超时兜底
        # （子进程 spawn 孙进程/持有 fd 副本时 EOF 后可能不退出——08-15 审查回归，
        #  原 H1 修复把 wait 移出 wait_for 丢失了超时保护，裸 wait 会永久挂起）
        try:
            returncode = await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            _kill_process_tree(proc)  # 同步函数（taskkill/killpg），勿 await
            msg = f"爬虫 {spider_name} 输出流已关闭但进程未退出（wait 10s 超时），已强制终止"
            logger.error("任务异常: task_id=%s %s", task_id, msg)
            await update_crawl_task(task_id, status="failed", error=msg[:500])
            await send_alert("crawl_timeout", msg, spider=spider_name)
            raise RuntimeError(msg)
    except asyncio.TimeoutError:
        _kill_process_tree(proc)  # 同步函数（taskkill/killpg），勿 await
        msg = f"爬虫 {spider_name} 超时（>{timeout}s），已强制终止"
        logger.error("任务超时: task_id=%s %s", task_id, msg)
        await update_crawl_task(task_id, status="failed", error=msg[:500])
        await send_alert("crawl_timeout", msg, spider=spider_name)
        raise RuntimeError(msg)
    logger.info("子进程退出: task_id=%s returncode=%s", task_id, returncode)

    if returncode != 0:
        detail = "\n".join(stderr_tail[-20:])[-2000:]
        msg = f"爬虫 {spider_name} 退出码 {returncode}: {detail}"
        logger.error("任务失败: task_id=%s %s", task_id, msg[:300])
        await update_crawl_task(task_id, status="failed", error=msg[:500])
        await send_alert("crawl_failed", msg, spider=spider_name, exit_code=returncode)
        raise RuntimeError(msg)

    # 统计产出条数（按行数）
    line_count = 0
    if output_file.exists():
        with output_file.open(encoding="utf-8") as f:
            line_count = sum(1 for _ in f)

    # 退出码 0 但无产出视为失败：爬虫"跑通"但未拿到数据，不能显示成功
    if line_count == 0:
        detail = "\n".join(stderr_tail[-20:])[-2000:]
        msg = f"爬虫 {spider_name} 产出 0 条数据: {detail}"
        logger.error("任务失败（无产出）: task_id=%s %s", task_id, msg[:300])
        await update_crawl_task(task_id, status="failed", error=msg[:500])
        await send_alert("crawl_failed", msg, spider=spider_name, items=0)
        raise RuntimeError(msg)

    logger.info("任务成功: task_id=%s spider=%s items=%s", task_id, spider_name, line_count)

    await update_crawl_task(
        task_id,
        status="success",
        progress=1.0,
        result={
            "spider": spider_name,
            "output_file": str(output_file.relative_to(_CRAWLERS_DIR.parent.parent)),
            "items": line_count,
            "crawled_at": timestamp,
        },
    )

    return {
        "spider": spider_name,
        "output_file": str(output_file.relative_to(_CRAWLERS_DIR.parent.parent)),
        "items": line_count,
        "crawled_at": timestamp,
    }


# 单爬虫独立调度当日幂等锁 TTL：覆盖最长爬虫执行窗口（zhilian 40min 上限）
_CRAWL_RUN_LOCK_TTL = 60 * 60 * 24


async def _crawl_run_lock_acquire(spider_name: str, run_date: str) -> bool:
    """单爬虫当日幂等锁（Redis SET NX，24h TTL）。返回 True=首次获得可执行。

    与 ETL 主管线锁（etl.py _etl_run_lock_acquire）同语义，但按 spider 隔离，
    避免独立 cron 触发与主管线同日重复跑同一爬虫。
    """
    from arq import create_pool
    from arq.connections import RedisSettings

    from app.core.config import settings

    client = await create_pool(RedisSettings.from_dsn(settings.arq_redis_url))
    try:
        acquired = await client.set(
            f"arq:crawl:run:{spider_name}:{run_date}",
            "1",
            nx=True,
            ex=_CRAWL_RUN_LOCK_TTL,
        )
        return bool(acquired)
    finally:
        await client.close()


async def _run_spider_if_scheduled(
    ctx: dict, spider: str, cfg: dict, run_date: str
) -> dict:
    """按单爬虫配置判断并触发（供 crawl_scheduler 每分钟调度复用）。

    - enabled=false → 跳过；
    - 未配置 hour/minute → 跳过（并入 ETL 主管线，不单独跑，防双跑）；
    - 当日已跑过 → 跳过（幂等锁）；
    - 否则按配置 max_results 触发 crawl_platform。
    """
    if cfg.get("enabled") is False:
        return {"spider": spider, "skipped": "disabled_by_config"}
    if "hour" not in cfg or "minute" not in cfg:
        return {"spider": spider, "skipped": "no_individual_schedule"}
    if not await _crawl_run_lock_acquire(spider, run_date):
        return {
            "spider": spider,
            "skipped": "duplicate_day_lock",
            "msg": f"当日 {spider} 已执行/在队列中，跳过重复触发",
        }
    return await crawl_platform(
        ctx,
        spider,
        max_results=cfg.get("max_results"),
    )


async def crawl_scheduler(ctx: dict) -> dict:
    """每爬虫独立 ARQ cron 入口（08-21b 每爬虫独立触发时间）。

    settings.cron_jobs 注册为每分钟 cron；此处按"当前 HH:MM == 配置 hour/minute"
    匹配到点的爬虫并触发。未配置独立时间的爬虫由 ETL 主管线统一触发，
    不在此重复——防双跑。当日幂等锁按 spider 隔离（_crawl_run_lock_acquire）。

    手动触发（/admin/crawl/trigger）仍走 crawl_platform 本体，不受本调度影响。
    """
    crawlers = runtime_config.get("crawlers") or {}
    if not isinstance(crawlers, dict):
        return {"run_date": None, "triggered": []}
    now = datetime.now(timezone(timedelta(hours=8)))
    current = (now.hour, now.minute)
    run_date = now.strftime("%Y-%m-%d")

    results: list[dict] = []
    for spider, cfg in crawlers.items():
        if not isinstance(cfg, dict):
            continue
        if (cfg.get("hour"), cfg.get("minute")) != current:
            continue
        results.append(await _run_spider_if_scheduled(ctx, spider, cfg, run_date))
    return {"run_date": run_date, "triggered": results}
