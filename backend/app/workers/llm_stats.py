"""LLM 调用统计日报任务：聚合 #454 审计计数 → reports/llm_stats_{date}.json。

链入 run_etl_pipeline 末段（阶段 17，dict_guard 之后），继承主管线当日幂等锁。
报告 only、无阈值动作；Redis 不可用/无数据时返回 skipped/error，不阻塞管线
（统计是旁路增强，与 llm_invocation fail-open 同语义）。

红线：报告为只读聚合，不含任何裁决逻辑；阈值告警待算法岗拍板后另行接入。
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))
_REPORT_DIR = Path(__file__).resolve().parents[2] / "reports"


async def llm_stats_daily(ctx: dict) -> dict:
    """LLM 调用统计日报（ARQ 注册名 llm_stats_daily）。

    统计窗口=自然日（ETL 05:00 跑昨日完整数据）。产出：
    - providers：per-provider 调用数/成功率/均延迟/各 outcome 分布（Redis）
    - purposes：per-purpose 调用分布（JSONL 明细，文件缺失时省略）
    """
    from app.services.extraction import llm_invocation
    from app.services.extraction.llm_stats import (
        aggregate_provider_stats,
        completeness_report_from_jsonl,
        latency_percentiles_from_jsonl,
        purpose_counts_from_jsonl,
    )

    now = datetime.now(_CST)
    run_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    prefix = f"llm:stats:{run_date}:"

    counts: dict[str, int] = {}
    try:
        client = llm_invocation._get_redis()
        for key in client.scan_iter(match=f"{prefix}*"):
            value = client.get(key)
            if value is not None:
                counts[key.decode() if isinstance(key, bytes) else key] = int(value)
    except Exception as e:
        return {"status": "error", "run_date": run_date, "error": str(e)[:200]}

    summary = {
        "status": "ok",
        "run_date": run_date,
        "generated_at": now.isoformat(timespec="seconds"),
        "providers": aggregate_provider_stats(counts),
    }

    # JSONL 明细 enrich（best-effort：api 容器可能未挂载 reports）
    jsonl = _REPORT_DIR / "llm_invocations" / f"{run_date}.jsonl"
    purposes = purpose_counts_from_jsonl(jsonl)
    if purposes:
        summary["purposes"] = purposes
    percentiles = latency_percentiles_from_jsonl(jsonl)
    if percentiles:
        summary["latency_percentiles"] = percentiles
    completeness = completeness_report_from_jsonl(jsonl)
    if completeness.get("entries"):
        summary["completeness"] = completeness

    _write_report(summary)
    logger.info("[llm_stats_daily] 报告已写入: %s", _report_path(run_date))
    return summary


def _report_path(run_date: str) -> Path:
    return _REPORT_DIR / f"llm_stats_{run_date}.json"


def _write_report(summary: dict) -> None:
    """报告落 backend/reports/llm_stats_{date}.json（幂等覆盖，同 quality 约定）。"""
    path = _report_path(summary["run_date"])
    try:
        _REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("llm_stats 日报写入失败（不影响管线）: %s", e)
