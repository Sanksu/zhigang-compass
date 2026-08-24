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


def latency_percentiles_from_jsonl(path: Path) -> dict[str, dict]:
    """JSONL 明细 → per-provider 时延分位数 {p50,p95,p99,n}（最近秩）。

    只统计真实调用尝试行（route=sync/fallback）；chain 汇总行不计入，
    避免把整链耗时重复算进最终 provider 的时延分布。文件缺失/空返回 {}。
    """
    per: dict[str, list[int]] = {}
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("route") == "chain":
                continue
            provider = str(entry.get("provider") or "?")
            per.setdefault(provider, []).append(max(0, int(entry.get("duration_ms") or 0)))

    result: dict[str, dict] = {}
    for provider, values in per.items():
        values.sort()
        n = len(values)

        def _quantile(p: float) -> int:
            # 最近秩：rank = ceil(p * n)，取第 rank 个有序值（1 基）
            rank = max(1, min(n, int(p * n) + (1 if p * n > int(p * n) else 0)))
            return values[rank - 1]

        result[provider] = {
            "p50": _quantile(0.50),
            "p95": _quantile(0.95),
            "p99": _quantile(0.99),
            "n": n,
        }
    return result


def completeness_report_from_jsonl(path: Path) -> dict:
    """JSONL 明细审计完整性：purpose/model 缺失与测试日志混入计数。

    正式验收门槛：purpose 不得为 unspecified、model 不得为空、test 环境
    行不得出现在生产日志——本函数只数数，由验收 gate 判定是否达标。
    """
    if not path.exists():
        return {"entries": 0, "unspecified_purpose": 0, "empty_model": 0, "test_env_entries": 0}
    total = unspecified = empty_model = test_env = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            if not entry.get("purpose") or entry.get("purpose") == "unspecified":
                unspecified += 1
            if not entry.get("model"):
                empty_model += 1
            if entry.get("env") == "test":
                test_env += 1
    return {
        "entries": total,
        "unspecified_purpose": unspecified,
        "empty_model": empty_model,
        "test_env_entries": test_env,
    }
