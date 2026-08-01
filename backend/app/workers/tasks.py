"""ARQ 异步任务定义。

任务类型（对齐设计文档 §4.4 ETL 管线）：
- ETL 编排：crawl_platform / run_etl_pipeline / validate_temporal / detect_inflation
- 业务异步：resume_parse / batch_extract / evolution_compute

设计要点：
- 爬虫通过 subprocess 调用 `scrapy crawl`，避免 Twisted reactor 与 asyncio loop 冲突
- ETL 任务编排采用 fail-fast：任一阶段失败立即抛出，由 ARQ 重试机制兜底
- 时滞/通胀检测 M2 仅交付框架，M3 LLM 抽取上线后接入真实数据
"""

import asyncio
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arq.connections import RedisSettings

from app.core.config import settings

# ── 爬虫项目根（backend/data/crawlers）──
_CRAWLERS_DIR = Path(__file__).resolve().parents[2] / "data" / "crawlers"
_OUTPUT_DIR = _CRAWLERS_DIR / "output"

# 显式消费 -a max_results 参数的 spider（其余源由各自默认采集量控制）
MAX_RESULTS_SUPPORTED = {"arxiv"}


# ============================================================
# ETL 阶段任务
# ============================================================

async def crawl_platform(
    ctx: dict,
    spider_name: str,
    keywords: list[str] | None = None,
    cities: list[str] | None = None,
    max_results: int | None = None,
) -> dict:
    """触发单个 Scrapy 爬虫。

    通过 subprocess 调用而非 in-process，原因：
    - Scrapy 基于 Twisted reactor，与 asyncio event loop 不兼容
    - subprocess 隔离崩溃，单爬虫失败不污染 worker

    输出：output/{spider}_{YYYYMMDD_HHMMSS}.jsonl
    """
    timestamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
    output_file = _OUTPUT_DIR / f"{spider_name}_{timestamp}.jsonl"
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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
            print(f"[crawl_platform] spider={spider_name} 不支持 max_results，参数已忽略", flush=True)

    # cwd 设到 crawlers/ 让 scrapy.cfg 生效
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(_CRAWLERS_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"爬虫 {spider_name} 退出码 {proc.returncode}: "
            f"{stderr.decode('utf-8', errors='replace')[-2000:]}"
        )

    # 统计产出条数（按行数）
    line_count = 0
    if output_file.exists():
        with output_file.open(encoding="utf-8") as f:
            line_count = sum(1 for _ in f)

    return {
        "spider": spider_name,
        "output_file": str(output_file.relative_to(_CRAWLERS_DIR.parent.parent)),
        "items": line_count,
        "crawled_at": timestamp,
    }


async def validate_temporal(ctx: dict, jd_ids: list[str]) -> dict:
    """时滞检测任务（设计文档 §4.7）。

    M2 阶段：调用 data_quality 模块的纯函数算法，数据来源为 mock 或图谱层。
    M3 阶段：从 jd_raw 表读取待评估 JD + 同岗位近 90 天历史，调用 detect_zombie_jd / detect_plagiarism。

    失败处置：标记 content_stale/obsolete/zombie/plagiarism 的 JD 写入 validation_report，
    降权系数写入 jd_raw.decay_weight（M3 业务表就位后）。
    """
    # M2 框架占位：仅记录任务被触发，实际检测在 ETL pipeline 中按需调用纯函数
    return {
        "status": "framework_only",
        "jd_ids": jd_ids,
        "msg": "M2 框架就绪，M3 接入 jd_raw + 图谱 first_seen_at 后启用",
    }


async def detect_inflation(ctx: dict, jd_ids: list[str]) -> dict:
    """通胀检测任务（设计文档 §4.8）。

    M2 阶段：框架占位，实际算法已在 data_quality.inflation_detector 实现。
    M3 阶段：从 jd_raw + LLM 抽取结果读取 job_level/min_years/skill_count/expert_level_count/education，
    调用 compute_inflation_score 输出 inflation_score + decay_weight。
    """
    return {
        "status": "framework_only",
        "jd_ids": jd_ids,
        "msg": "M2 框架就绪，M3 LLM 抽取上线后接入四维数据",
    }


async def run_etl_pipeline(ctx: dict, run_date: str | None = None) -> dict:
    """编排完整 ETL 管线（设计文档 §4.4）。

    管线顺序：
        crawl_jds → clean_jds(已在 Scrapy Pipeline 内嵌) → dedup
        → validate_temporal → detect_inflation → structure → load_to_db → load_to_neo4j

    M2 阶段：仅执行 crawl_jds + 框架占位任务（structure/load 依赖 M3 LLM 抽取）
    M3 阶段：完整管线启用

    Args:
        run_date: 调度日期 YYYY-MM-DD，None 时取 UTC+8 当日
    """
    if run_date is None:
        run_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    # 按设计文档 §4.4 数据更新频率分组
    # 国内 A 级 + B 级（02:00 / 04:00）
    domestic_platforms = ["boss", "zhilian"]
    # 国际 A/B 级（错峰）
    international_platforms = ["monster", "indeed", "glassdoor"]
    # 非招聘数据源（论文/社区/课程）
    trend_platforms = ["arxiv", "github", "stackoverflow"]

    results: dict = {
        "run_date": run_date,
        "stages": {},
    }

    # ── 阶段 1：爬虫（A 级国内主源）──
    crawl_results = []
    for spider in domestic_platforms + international_platforms + trend_platforms:
        try:
            r = await crawl_platform(ctx, spider)
            crawl_results.append(r)
        except Exception as e:
            # 单源失败不阻塞其他源（设计文档「单源失效不影响整体」）
            crawl_results.append({"spider": spider, "error": str(e)})
    results["stages"]["crawl"] = crawl_results

    # ── 阶段 2：清洗 + 去重 ──
    # 已嵌入 Scrapy CleaningPipeline（SHA256 指纹 upsert 即去重）
    # SimHash 去重为 DA-M3-08 遗留项
    results["stages"]["clean_dedup"] = {
        "status": "embedded_in_scrapy_pipeline",
        "simhash_pending": "DA-M3-08",
    }

    # ── 阶段 3：时滞检测（M3 启用）──
    results["stages"]["validate_temporal"] = await validate_temporal(ctx, jd_ids=[])

    # ── 阶段 4：通胀检测（M3 启用）──
    results["stages"]["detect_inflation"] = await detect_inflation(ctx, jd_ids=[])

    # ── 阶段 5：结构化 + 入库（M3 启用）──
    results["stages"]["structure_load"] = {
        "status": "pending_m3",
        "msg": "依赖 AL-M3-01 LLM 抽取上线 + AL-M3-09 JD 入图",
    }

    return results


# ============================================================
# 业务异步任务（M3/M4 实现）
# ============================================================

async def resume_parse(ctx: dict, file_path: str) -> dict:
    """简历解析异步任务（M4 实现，当前未交付）。

    未实现时显式抛错（任务标记 failed），不做假成功返回。
    """
    raise NotImplementedError("resume_parse 待 M4 实现（pypdf/python-docx/OCR + PII 脱敏 + LLM 抽取）")


async def batch_extract(ctx: dict, jd_ids: list[str]) -> dict:
    """LLM 批量实体抽取异步任务（M3 实现，依赖 AL-M3-01，当前未交付）。"""
    raise NotImplementedError("batch_extract 待 AL-M3-01 LLM 抽取上线后实现")


async def evolution_compute(ctx: dict, version: str) -> dict:
    """每日演化计算异步任务（M3 实现，当前未交付）。"""
    raise NotImplementedError("evolution_compute 待 AL-M3 演化管线接入后实现")


# ============================================================
# ARQ Worker 注册
# ============================================================

async def on_startup(ctx: dict) -> None:
    """Worker 启动钩子。"""
    print(f"[ARQ Worker] 启动，PID={ctx.get('worker_pid')}")


async def on_shutdown(ctx: dict) -> None:
    """Worker 关闭钩子。"""
    print("[ARQ Worker] 关闭")


class WorkerSettings:
    """ARQ Worker 配置。

    启动命令：arq app.workers.tasks.WorkerSettings
    """
    functions = [
        crawl_platform,
        run_etl_pipeline,
        validate_temporal,
        detect_inflation,
        resume_parse,
        batch_extract,
        evolution_compute,
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(settings.arq_redis_url)
    concurrency = settings.arq_concurrency
    task_timeout = settings.arq_task_timeout
    max_retries = 2
    retry_delay = 10
