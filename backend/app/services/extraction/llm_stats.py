"""LLM 调用统计聚合（P1-2 可观测：#454 审计数据 → 日报结构）。

数据源（llm_invocation 写入侧）：
- Redis 计数：llm:stats:{ymd}:{provider}:{outcome|calls_total|latency_ms_sum}
- JSONL 明细：reports/llm_invocations/{ymd}.jsonl（含 purpose 维度）

纯函数在本模块，IO 编排在 workers/llm_stats.py（对齐 dict_guard 分层）。
报告 only、无阈值动作——告警/门禁阈值待算法岗拍板后另行接入。
"""

import json
from pathlib import Path

# Redis key 前缀（与 llm_invocation 写入侧一致）
_KEY_PREFIX = "llm:stats:"
# 非 outcome 维度的计数键
_TOTAL_KEY = "calls_total"
_LATENCY_KEY = "latency_ms_sum"


def aggregate_provider_stats(counts: dict[str, int]) -> dict:
    """单日 provider 计数 → 汇总结构。

    Args:
        counts: {redis key: value}，key 形如 llm:stats:{ymd}:{provider}:{metric}

    Returns:
        {provider: {calls_total, ok, by_outcome: {...}, ok_rate, avg_latency_ms}}
    """
    providers: dict[str, dict] = {}
    for key, value in counts.items():
        parts = key.split(":")
        # llm / stats / ymd / provider / metric（provider 名不含冒号，配置校验保证）
        if len(parts) != 5 or not key.startswith(_KEY_PREFIX):
            continue
        _, _, _, provider, metric = parts
        entry = providers.setdefault(
            provider, {"calls_total": 0, "by_outcome": {}, "latency_ms_sum": 0}
        )
        if metric == _TOTAL_KEY:
            entry["calls_total"] = int(value)
        elif metric == _LATENCY_KEY:
            entry["latency_ms_sum"] = int(value)
        else:
            entry["by_outcome"][metric] = int(value)

    for entry in providers.values():
        latency = entry.pop("latency_ms_sum")
        total = entry["calls_total"]
        ok = entry["by_outcome"].get("ok", 0)
        entry["ok_rate"] = round(ok / total, 4) if total else None
        entry["avg_latency_ms"] = round(latency / total) if total else None
    return providers


def purpose_counts_from_jsonl(path: Path) -> dict[str, int]:
    """JSONL 明细 → purpose 调用计数（坏行跳过；文件缺失返回空）。"""
    if not path.exists():
        return {}
    counts: dict[str, int] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            purpose = str(entry.get("purpose") or "unspecified")
            counts[purpose] = counts.get(purpose, 0) + 1
    return counts
