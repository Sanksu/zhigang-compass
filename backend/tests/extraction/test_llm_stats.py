"""LLM 调用统计聚合单元测试（aggregate_provider_stats + purpose_counts_from_jsonl）。"""

import json

from app.services.extraction.llm_stats import (
    aggregate_provider_stats,
    purpose_counts_from_jsonl,
)


def _counts() -> dict[str, int]:
    return {
        "llm:stats:2026-08-22:primary:ok": 8,
        "llm:stats:2026-08-22:primary:timeout": 1,
        "llm:stats:2026-08-22:primary:calls_total": 9,
        "llm:stats:2026-08-22:primary:latency_ms_sum": 4500,
        "llm:stats:2026-08-22:backup:rate_limited": 2,
        "llm:stats:2026-08-22:backup:circuit_skipped": 1,
        "llm:stats:2026-08-22:backup:calls_total": 3,
        "llm:stats:2026-08-22:backup:latency_ms_sum": 600,
    }


class TestAggregateProviderStats:
    def test_per_provider_summary(self):
        out = aggregate_provider_stats(_counts())
        assert set(out) == {"primary", "backup"}

        primary = out["primary"]
        assert primary["calls_total"] == 9
        assert primary["by_outcome"] == {"ok": 8, "timeout": 1}
        assert primary["ok_rate"] == round(8 / 9, 4)
        assert primary["avg_latency_ms"] == 500

    def test_no_success_yields_zero_ok_rate(self):
        out = aggregate_provider_stats(_counts())
        backup = out["backup"]
        assert backup["ok_rate"] == 0.0
        assert "circuit_skipped" in backup["by_outcome"]

    def test_empty_counts(self):
        assert aggregate_provider_stats({}) == {}

    def test_foreign_keys_ignored(self):
        out = aggregate_provider_stats({
            "llm:circuit:primary": 1,          # 非 stats 键
            "llm:stats:2026-08-22:p:ok": 2,    # 合法键
            "unrelated:key": 5,
        })
        assert set(out) == {"p"}

    def test_latency_none_when_no_calls(self):
        # 只有 latency 累计、无 calls_total（理论脏数据）→ 不除零
        out = aggregate_provider_stats({"llm:stats:2026-08-22:x:latency_ms_sum": 10})
        assert out["x"]["avg_latency_ms"] is None
        assert out["x"]["ok_rate"] is None


class TestPurposeCountsFromJsonl:
    def test_counts_by_purpose(self, tmp_path):
        path = tmp_path / "2026-08-22.jsonl"
        lines = [
            {"purpose": "jd_extract", "outcome": "ok"},
            {"purpose": "jd_extract", "outcome": "timeout"},
            {"purpose": "dict_guard"},
            {"purpose": None},
            "not-json",
            "",
        ]
        path.write_text(
            "\n".join(json.dumps(x) if isinstance(x, dict) else x for x in lines),
            encoding="utf-8",
        )
        counts = purpose_counts_from_jsonl(path)
        assert counts == {"jd_extract": 2, "dict_guard": 1, "unspecified": 1}

    def test_missing_file_returns_empty(self, tmp_path):
        assert purpose_counts_from_jsonl(tmp_path / "nope.jsonl") == {}
