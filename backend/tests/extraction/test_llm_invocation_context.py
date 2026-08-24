"""调用上下文扩展与运维统计（PR1b：run/version/entity/env + 链汇总 + 分位/完整性）。

覆盖：
- invocation_scope 携带 run_id/version/entity_ref/env 维度并正确还原；
- record() 自动带入上下文与 env（pytest 进程 → test，避免生产/测试日志混入）；
- record_chain 只落 JSONL、不写 Redis 计数（不污染 per-provider 时延均值）；
- llm_stats 分位数（最近秩）与完整性审计计数。
"""

import json

import pytest
import yaml
from pathlib import Path

from app.services.extraction import llm_invocation
from app.services.extraction import llm_provider as llm_provider_module
from app.services.extraction.llm_invocation import invocation_scope
from app.services.extraction.llm_provider import (
    LLMProviderChain,
)
from app.services.extraction import llm_stats


class _DemoModel(llm_provider_module.BaseModel):
    value: str


@pytest.fixture(autouse=True)
def _reset_sink_state(monkeypatch):
    monkeypatch.setattr(llm_invocation, "_sink_disabled", False)


class _FakeRedis:
    def __init__(self):
        self.ops: list = []

    def pipeline(self, transaction=False):
        class _P:
            def incr(self, key):
                self.ops.append(("incr", key))

            def incrby(self, key, amount):
                self.ops.append(("incrby", key, amount))

            def expire(self, key, ttl):
                self.ops.append(("expire", key, ttl))

            def execute(self):
                return []

        return _P()


@pytest.fixture
def audit_env(tmp_path, monkeypatch):
    sink = tmp_path / "llm_invocations"
    monkeypatch.setattr(llm_invocation, "_SINK_DIR", sink)
    fake = _FakeRedis()
    monkeypatch.setattr(llm_invocation, "_redis_client", fake)
    return sink, fake


def _read_lines(sink) -> list[dict]:
    files = sorted(sink.glob("*.jsonl"))
    assert len(files) == 1
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


class TestScopeContext:
    def test_scope_carries_context_and_restores(self):
        assert llm_invocation.current_run_id() == ""
        with invocation_scope(
            "skill_classify", run_id="run-1", version="v3", entity_ref="sk:42",
        ):
            assert llm_invocation.current_run_id() == "run-1"
            assert llm_invocation.current_version() == "v3"
            assert llm_invocation.current_entity_ref() == "sk:42"
        assert llm_invocation.current_run_id() == ""
        assert llm_invocation.current_version() == ""

    def test_scope_env_override_and_default(self, monkeypatch):
        assert llm_invocation._current_env() == "test"  # pytest 进程
        with invocation_scope("jd_extract", env="production"):
            assert llm_invocation._current_env() == "production"
        monkeypatch.setattr(llm_invocation, "_env", llm_invocation._env)  # 还原语义不变


class TestRecordContextFields:
    def test_record_carries_scope_context(self, audit_env):
        sink, _ = audit_env
        with invocation_scope(
            "governance", run_id="run-9", version="v1", entity_ref="skill:term",
        ):
            llm_invocation.record(
                route="fallback", provider="p", model="m", attempt=1,
                outcome="ok", duration_ms=5,
            )
        entry = _read_lines(sink)[0]
        assert entry["run_id"] == "run-9"
        assert entry["version"] == "v1"
        assert entry["entity_ref"] == "skill:term"
        assert entry["env"] == "test"

    def test_chain_summary_skips_redis_counters(self, audit_env):
        sink, fake = audit_env
        with invocation_scope("jd_extract"):
            llm_invocation.record_chain(provider="deepseek", outcome="ok", duration_ms=9000)
        entries = _read_lines(sink)
        assert len(entries) == 1
        assert entries[0]["route"] == "chain"
        assert entries[0]["provider"] == "deepseek"
        # Redis 无任何计数操作（chain 不参与 per-provider 统计）
        assert fake.ops == []


def _write_config(path, providers):
    path.write_text(
        yaml.safe_dump({"providers": providers}, allow_unicode=True),
        encoding="utf-8",
    )


class TestChainSummaryFromProvider:
    def test_success_records_chain_row(self, tmp_path, audit_env, monkeypatch):
        sink, _ = audit_env
        path = tmp_path / "llm.yaml"
        _write_config(path, [
            {"name": "primary", "priority": 1, "api_key": "k1", "enabled": True},
        ])
        chain = LLMProviderChain(config_path=path)
        monkeypatch.setattr(llm_provider_module, "_is_skipped", lambda name: None)

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            return _DemoModel(value="ok")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        with invocation_scope("jd_extract", run_id="run-x"):
            chain.call_with_fallback("prompt", _DemoModel)
        entries = _read_lines(sink)
        chains = [e for e in entries if e["route"] == "chain"]
        assert len(chains) == 1
        assert chains[0]["outcome"] == "ok"
        assert chains[0]["provider"] == "primary"
        assert chains[0]["run_id"] == "run-x"
        assert chains[0]["duration_ms"] >= 0


class TestPercentilesAndCompleteness:
    def _write(self, tmp_path, lines: list[dict]) -> Path:
        path = tmp_path / "2026-08-24.jsonl"
        path.write_text(
            "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines),
            encoding="utf-8",
        )
        return path

    def test_percentiles_nearest_rank(self, tmp_path):
        # 时延 1..10：p50=5, p95=10, p99=10（最近秩：rank=ceil(p*n)）
        lines = [
            {"route": "fallback", "provider": "deepseek", "duration_ms": i}
            for i in range(1, 11)
        ]
        path = self._write(tmp_path, lines)
        got = llm_stats.latency_percentiles_from_jsonl(path)
        assert got["deepseek"] == {"p50": 5, "p95": 10, "p99": 10, "n": 10}

    def test_percentiles_exclude_chain_rows(self, tmp_path):
        lines = [
            {"route": "fallback", "provider": "deepseek", "duration_ms": 1},
            {"route": "chain", "provider": "deepseek", "duration_ms": 999},
        ]
        path = self._write(tmp_path, lines)
        got = llm_stats.latency_percentiles_from_jsonl(path)
        assert got["deepseek"]["n"] == 1
        assert got["deepseek"]["p95"] == 1

    def test_percentiles_missing_file_empty(self, tmp_path):
        assert llm_stats.latency_percentiles_from_jsonl(tmp_path / "absent.jsonl") == {}

    def test_completeness_counts(self, tmp_path):
        lines = [
            {"purpose": "jd_extract", "model": "m", "env": "production"},
            {"purpose": "unspecified", "model": "m", "env": "production"},
            {"purpose": "jd_extract", "model": "", "env": "test"},
        ]
        path = self._write(tmp_path, lines)
        got = llm_stats.completeness_report_from_jsonl(path)
        assert got["entries"] == 3
        assert got["unspecified_purpose"] == 1
        assert got["empty_model"] == 1
        assert got["test_env_entries"] == 1

    def test_completeness_missing_file(self, tmp_path):
        got = llm_stats.completeness_report_from_jsonl(tmp_path / "absent.jsonl")
        assert got["entries"] == 0