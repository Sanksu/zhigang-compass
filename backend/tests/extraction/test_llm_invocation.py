"""LLM 调用审计单元测试（llm_invocation 记录器 + provider 链埋点）。

覆盖：purpose 作用域、JSONL 落盘字段、Redis 聚合计数、fail-open（落盘失败
停用不抛错）、outcome 归类优先级，以及 call_sync/call_with_fallback 的
尝试/跳过事件记录。外部 API 仍不在本层测试（对齐 test_llm_provider 约定）。
"""

import json
from pathlib import Path

import pytest
import yaml

from app.services.extraction import llm_invocation
from app.services.extraction import llm_provider as llm_provider_module
from app.services.extraction.llm_invocation import invocation_scope
from app.services.extraction.llm_provider import (
    LLMExtractionError,
    LLMProviderChain,
    LLMRateLimitError,
    LLMTimeoutError,
    _with_outcome,
)


class _DemoModel(llm_provider_module.BaseModel):
    value: str


@pytest.fixture(autouse=True)
def _reset_sink_state(monkeypatch):
    """模块级停用标记跨用例复位（落盘失败测试会置 True 污染后续用例）。"""
    monkeypatch.setattr(llm_invocation, "_sink_disabled", False)


class _FakePipeline:
    """收集 pipeline 命令的桩（incr/incrby/expire）。"""

    def __init__(self, ops: list):
        self._ops = ops

    def incr(self, key):
        self._ops.append(("incr", key))

    def incrby(self, key, amount):
        self._ops.append(("incrby", key, amount))

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))

    def execute(self):
        return []


class _FakeRedis:
    def __init__(self):
        self.ops: list = []

    def pipeline(self, transaction=False):
        return _FakePipeline(self.ops)


@pytest.fixture
def audit_env(tmp_path, monkeypatch):
    """指向 tmp_path 的 JSONL 池 + 桩 Redis；返回 (sink_dir, fake_redis)。"""
    sink = tmp_path / "llm_invocations"
    monkeypatch.setattr(llm_invocation, "_SINK_DIR", sink)
    fake = _FakeRedis()
    monkeypatch.setattr(llm_invocation, "_redis_client", fake)
    return sink, fake


def _read_lines(sink: Path) -> list[dict]:
    files = sorted(sink.glob("*.jsonl"))
    assert len(files) == 1
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


class TestInvocationScope:
    def test_scope_sets_and_restores_purpose(self):
        assert llm_invocation.current_purpose() == "unspecified"
        with invocation_scope("jd_extract"):
            assert llm_invocation.current_purpose() == "jd_extract"
            with invocation_scope("cluster_label"):
                assert llm_invocation.current_purpose() == "cluster_label"
            assert llm_invocation.current_purpose() == "jd_extract"
        assert llm_invocation.current_purpose() == "unspecified"

    def test_empty_purpose_falls_back(self):
        with invocation_scope(""):
            assert llm_invocation.current_purpose() == "unspecified"


class TestRecorder:
    def test_record_writes_jsonl_and_redis_counters(self, audit_env):
        sink, fake = audit_env
        with invocation_scope("dict_guard"):
            llm_invocation.record(
                route="fallback", provider="primary", model="demo-model",
                attempt=1, outcome="ok", duration_ms=123,
            )
        entries = _read_lines(sink)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["route"] == "fallback"
        assert entry["purpose"] == "dict_guard"
        assert entry["provider"] == "primary"
        assert entry["model"] == "demo-model"
        assert entry["attempt"] == 1
        assert entry["outcome"] == "ok"
        assert entry["duration_ms"] == 123
        assert entry["error"] is None

        base = f"llm:stats:{entry['ts'][:10]}:primary"
        keys = [op[1] for op in fake.ops if op[0] in ("incr", "incrby")]
        assert f"{base}:ok" in keys
        assert f"{base}:calls_total" in keys

    def test_error_field_truncated_and_null_when_empty(self, audit_env):
        sink, _ = audit_env
        long_error = "x" * 500
        llm_invocation.record(
            route="sync", provider="p", model="m", attempt=1,
            outcome="timeout", duration_ms=1, error=long_error,
        )
        llm_invocation.record(
            route="sync", provider="p", model="m", attempt=1,
            outcome="timeout", duration_ms=1, error="",
        )
        entries = _read_lines(sink)
        assert len(entries[0]["error"]) == 200
        assert entries[1]["error"] is None

    def test_sink_failure_disables_without_raise(self, tmp_path, monkeypatch):
        # 指向一个"文件"作为目录 → mkdir 失败 → 首次失败停用，后续静默
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir", encoding="utf-8")
        monkeypatch.setattr(llm_invocation, "_SINK_DIR", blocker / "sub")
        fake = _FakeRedis()
        monkeypatch.setattr(llm_invocation, "_redis_client", fake)
        llm_invocation.record(
            route="sync", provider="p", model="m", attempt=1,
            outcome="ok", duration_ms=1,
        )
        llm_invocation.record(
            route="sync", provider="p", model="m", attempt=1,
            outcome="timeout", duration_ms=2,
        )
        assert llm_invocation._sink_disabled is True


class TestOutcomeClassification:
    def test_attached_outcome_takes_precedence(self):
        err = _with_outcome(LLMExtractionError("限流误标"), "rate_limited")
        assert llm_provider_module._outcome_of(err) == "rate_limited"

    def test_type_fallback_mapping(self):
        assert llm_provider_module._outcome_of(LLMTimeoutError("t")) == "timeout"
        assert llm_provider_module._outcome_of(LLMRateLimitError("r")) == "rate_limited"
        assert llm_provider_module._outcome_of(None) == "ok"

    def test_with_outcome_returns_same_exception(self):
        err = LLMExtractionError("e")
        assert _with_outcome(err, "connection_error") is err
        assert err.outcome == "connection_error"


def _write_config(path: Path, providers: list[dict]) -> None:
    path.write_text(
        yaml.safe_dump({"providers": providers}, allow_unicode=True),
        encoding="utf-8",
    )


def _make_chain(tmp_path: Path) -> LLMProviderChain:
    path = tmp_path / "llm.yaml"
    _write_config(path, [
        {"name": "primary", "priority": 1, "api_key": "k1", "enabled": True},
        {"name": "backup", "priority": 2, "api_key": "k2", "enabled": True},
    ])
    return LLMProviderChain(config_path=path)


class TestChainInstrumentation:
    def test_sync_success_records_ok(self, tmp_path, audit_env, monkeypatch):
        sink, _ = audit_env
        chain = _make_chain(tmp_path)
        monkeypatch.setattr(llm_provider_module, "_is_skipped", lambda name: None)

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            return _DemoModel(value="ok")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        result = chain.call_sync("prompt", _DemoModel)
        assert result.value == "ok"
        entries = _read_lines(sink)
        assert len(entries) == 1
        assert entries[0]["outcome"] == "ok"
        assert entries[0]["route"] == "sync"
        assert entries[0]["provider"] == "primary"

    def test_sync_rate_limit_records_rate_limited(self, tmp_path, audit_env, monkeypatch):
        sink, _ = audit_env
        chain = _make_chain(tmp_path)

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            raise LLMRateLimitError("429")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        monkeypatch.setattr(llm_provider_module, "_is_skipped", lambda name: None)
        with pytest.raises(LLMTimeoutError):
            chain.call_sync("prompt", _DemoModel)
        entries = _read_lines(sink)
        assert len(entries) == 1
        # 同步路由把 429 映射为 504 契约不变，但审计保留真实归类
        assert entries[0]["outcome"] == "rate_limited"

    def test_fallback_attempts_recorded_per_provider(self, tmp_path, audit_env, monkeypatch):
        sink, _ = audit_env
        chain = _make_chain(tmp_path)
        monkeypatch.setattr(llm_provider_module, "_is_skipped", lambda name: None)

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            raise LLMTimeoutError("超时")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        monkeypatch.setattr(llm_provider_module, "_is_skipped", lambda name: None)
        with pytest.raises(LLMTimeoutError):
            chain.call_with_fallback("prompt", _DemoModel)
        entries = _read_lines(sink)
        attempts = [e for e in entries if e["route"] != "chain"]
        assert [(e["provider"], e["attempt"]) for e in attempts] == [
            ("primary", 1), ("backup", 2),
        ]
        assert all(e["outcome"] == "timeout" for e in attempts)
        # 链汇总行：全链失败 provider=""，record_chain 只落 JSONL
        chain_rows = [e for e in entries if e["route"] == "chain"]
        assert len(chain_rows) == 1
        assert chain_rows[0]["provider"] == ""
        assert chain_rows[0]["outcome"] == "failed"

    def test_circuit_skip_event_recorded_attempt_zero(self, tmp_path, audit_env, monkeypatch):
        sink, _ = audit_env
        chain = _make_chain(tmp_path)
        monkeypatch.setattr(llm_provider_module, "_is_skipped", lambda name: "circuit")
        with pytest.raises(LLMTimeoutError):
            chain.call_sync("prompt", _DemoModel)
        entries = _read_lines(sink)
        assert entries[0]["attempt"] == 0
        assert entries[0]["outcome"] == "circuit_skipped"
        assert entries[0]["duration_ms"] == 0

    def test_records_carry_declared_purpose(self, tmp_path, audit_env, monkeypatch):
        sink, _ = audit_env
        chain = _make_chain(tmp_path)
        monkeypatch.setattr(llm_provider_module, "_is_skipped", lambda name: None)

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            return _DemoModel(value="ok")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        with invocation_scope("jd_extract_batch"):
            chain.call_sync("prompt", _DemoModel)
        entries = _read_lines(sink)
        assert entries[0]["purpose"] == "jd_extract_batch"
