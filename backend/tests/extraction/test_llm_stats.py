"""LLM 调用统计聚合单元测试（aggregate_provider_stats + purpose_counts_from_jsonl + latency_percentiles_from_jsonl + completeness_report_from_jsonl）。"""

import json

from app.services.extraction.llm_stats import (
    aggregate_provider_stats,
    completeness_report_from_jsonl,
    latency_percentiles_from_jsonl,
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


class TestLatencyPercentilesFromJsonl:
    def test_per_provider_percentiles(self, tmp_path):
        path = tmp_path / "2026-08-22.jsonl"
        # 10 条 primary，5 条 backup；一条 chain 汇总行应被跳过
        lines = [
            {"provider": "primary", "duration_ms": 100, "route": "sync"},
            {"provider": "primary", "duration_ms": 200, "route": "sync"},
            {"provider": "primary", "duration_ms": 300, "route": "sync"},
            {"provider": "primary", "duration_ms": 400, "route": "sync"},
            {"provider": "primary", "duration_ms": 500, "route": "sync"},
            {"provider": "primary", "duration_ms": 600, "route": "sync"},
            {"provider": "primary", "duration_ms": 700, "route": "sync"},
            {"provider": "primary", "duration_ms": 800, "route": "sync"},
            {"provider": "primary", "duration_ms": 900, "route": "sync"},
            {"provider": "primary", "duration_ms": 1000, "route": "sync"},
            {"provider": "backup", "duration_ms": 50, "route": "fallback"},
            {"provider": "backup", "duration_ms": 150, "route": "fallback"},
            {"provider": "backup", "duration_ms": 250, "route": "fallback"},
            {"provider": "backup", "duration_ms": 350, "route": "fallback"},
            {"provider": "backup", "duration_ms": 450, "route": "fallback"},
            {"provider": "primary", "duration_ms": 9999, "route": "chain"},  # chain 行跳过
            "not-json",
            "",
        ]
        path.write_text(
            "\n".join(json.dumps(x) if isinstance(x, dict) else x for x in lines),
            encoding="utf-8",
        )
        result = latency_percentiles_from_jsonl(path)
        assert set(result) == {"primary", "backup"}

        primary = result["primary"]
        assert primary["n"] == 10
        # 最近秩：n=10, p50 → rank=5 → 第5个值=500
        assert primary["p50"] == 500
        # p95 → rank=ceil(9.5)=10 → 第10个值=1000
        assert primary["p95"] == 1000
        # p99 → rank=ceil(9.9)=10 → 1000
        assert primary["p99"] == 1000

        backup = result["backup"]
        assert backup["n"] == 5
        # p50 → rank=ceil(2.5)=3 → 第3个值=250
        assert backup["p50"] == 250
        # p95 → rank=ceil(4.75)=5 → 450
        assert backup["p95"] == 450

    def test_missing_file_returns_empty(self, tmp_path):
        assert latency_percentiles_from_jsonl(tmp_path / "nope.jsonl") == {}

    def test_empty_file_returns_empty(self, tmp_path):
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        assert latency_percentiles_from_jsonl(path) == {}

    def test_only_chain_entries_returns_empty(self, tmp_path):
        path = tmp_path / "chain_only.jsonl"
        lines = [
            {"provider": "p1", "duration_ms": 100, "route": "chain"},
            {"provider": "p2", "duration_ms": 200, "route": "chain"},
        ]
        path.write_text(
            "\n".join(json.dumps(x) for x in lines),
            encoding="utf-8",
        )
        assert latency_percentiles_from_jsonl(path) == {}

    def test_negative_duration_clamped_to_zero(self, tmp_path):
        path = tmp_path / "neg.jsonl"
        lines = [
            {"provider": "p1", "duration_ms": -50, "route": "sync"},
            {"provider": "p1", "duration_ms": 100, "route": "sync"},
        ]
        path.write_text(
            "\n".join(json.dumps(x) for x in lines),
            encoding="utf-8",
        )
        result = latency_percentiles_from_jsonl(path)
        assert result["p1"]["n"] == 2
        # 排序后: [0, 100]，p50 → rank=ceil(1)=1 → 0
        assert result["p1"]["p50"] == 0

    def test_missing_provider_defaults_to_question(self, tmp_path):
        path = tmp_path / "no_provider.jsonl"
        lines = [
            {"duration_ms": 100, "route": "sync"},
            {"provider": None, "duration_ms": 200, "route": "sync"},
        ]
        path.write_text(
            "\n".join(json.dumps(x) for x in lines),
            encoding="utf-8",
        )
        result = latency_percentiles_from_jsonl(path)
        assert "?" in result
        assert result["?"]["n"] == 2

    def test_single_entry(self, tmp_path):
        path = tmp_path / "single.jsonl"
        path.write_text(
            json.dumps({"provider": "p1", "duration_ms": 42, "route": "sync"}),
            encoding="utf-8",
        )
        result = latency_percentiles_from_jsonl(path)
        assert result["p1"] == {"p50": 42, "p95": 42, "p99": 42, "n": 1}


class TestCompletenessReportFromJsonl:
    def test_full_metrics(self, tmp_path):
        path = tmp_path / "2026-08-22.jsonl"
        lines = [
            {"purpose": "jd_extract", "model": "gpt-4", "env": "production"},
            {"purpose": "dict_guard", "model": "gpt-3.5", "env": "production"},
            {"purpose": None, "model": "gpt-4", "env": "production"},        # unspecified
            {"purpose": "unspecified", "model": "gpt-4", "env": "production"},  # unspecified
            {"purpose": "jd_extract", "model": "", "env": "production"},     # empty_model
            {"purpose": "jd_extract", "model": None, "env": "production"},   # empty_model
            {"purpose": "jd_extract", "model": "gpt-4", "env": "test"},      # test_env
            "not-json",
            "",
        ]
        path.write_text(
            "\n".join(json.dumps(x) if isinstance(x, dict) else x for x in lines),
            encoding="utf-8",
        )
        report = completeness_report_from_jsonl(path)
        assert report["entries"] == 7
        assert report["unspecified_purpose"] == 2
        assert report["empty_model"] == 2
        assert report["test_env_entries"] == 1

    def test_missing_file_returns_zeros(self, tmp_path):
        report = completeness_report_from_jsonl(tmp_path / "nope.jsonl")
        assert report == {
            "entries": 0,
            "unspecified_purpose": 0,
            "empty_model": 0,
            "test_env_entries": 0,
        }

    def test_empty_file_returns_zeros(self, tmp_path):
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        report = completeness_report_from_jsonl(path)
        assert report["entries"] == 0
        assert report["unspecified_purpose"] == 0
        assert report["empty_model"] == 0
        assert report["test_env_entries"] == 0

    def test_all_good_entries(self, tmp_path):
        path = tmp_path / "all_good.jsonl"
        lines = [
            {"purpose": "jd_extract", "model": "gpt-4", "env": "production"},
            {"purpose": "dict_guard", "model": "gpt-3.5", "env": "staging"},
            {"purpose": "resume_parse", "model": "claude-3", "env": "production"},
        ]
        path.write_text(
            "\n".join(json.dumps(x) for x in lines),
            encoding="utf-8",
        )
        report = completeness_report_from_jsonl(path)
        assert report["entries"] == 3
        assert report["unspecified_purpose"] == 0
        assert report["empty_model"] == 0
        assert report["test_env_entries"] == 0

    def test_all_bad_entries(self, tmp_path):
        path = tmp_path / "all_bad.jsonl"
        lines = [
            {"purpose": "", "model": "", "env": "test"},
            {"purpose": None, "model": None, "env": "test"},
            {"purpose": "unspecified", "model": "", "env": "test"},
        ]
        path.write_text(
            "\n".join(json.dumps(x) for x in lines),
            encoding="utf-8",
        )
        report = completeness_report_from_jsonl(path)
        assert report["entries"] == 3
        assert report["unspecified_purpose"] == 3
        assert report["empty_model"] == 3
        assert report["test_env_entries"] == 3

    def test_invalid_json_skipped_not_counted(self, tmp_path):
        path = tmp_path / "bad_json.jsonl"
        lines = [
            '{"purpose": "jd_extract", "model": "gpt-4"}',
            'this is not json',
            '{"purpose": "dict_guard"}',
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        report = completeness_report_from_jsonl(path)
        assert report["entries"] == 2
