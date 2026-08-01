"""LLMProviderChain 单元测试（设计文档 §6.5 多 Provider 重试链）。

覆盖：yaml 加载（priority 排序 / enabled 过滤）、未配置抛错、
call_sync 单次尝试不切换、call_with_fallback 按优先级切换、全失败聚合。
外部 API 调用（instructor/openai）不在此层测试，仅测链语义。
"""

from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from app.services.extraction.llm_provider import (
    LLMConfigurationError,
    LLMExtractionError,
    LLMProviderChain,
    LLMTimeoutError,
)


class _DemoModel(BaseModel):
    """测试用的响应模型。"""

    value: str


def _write_config(path: Path, providers: list[dict]) -> None:
    path.write_text(
        yaml.safe_dump({"providers": providers}, allow_unicode=True),
        encoding="utf-8",
    )


def _make_chain(tmp_path: Path) -> tuple[LLMProviderChain, Path]:
    path = tmp_path / "llm.yaml"
    _write_config(path, [
        {"name": "primary", "priority": 1, "api_key": "k1", "enabled": True},
        {"name": "backup", "priority": 2, "api_key": "k2", "enabled": True},
    ])
    return LLMProviderChain(config_path=path), path


class TestLoadProviders:
    def test_sorted_by_priority_and_filtered_by_enabled(self, tmp_path):
        path = tmp_path / "llm.yaml"
        _write_config(path, [
            {"name": "c", "priority": 3, "api_key": "k3", "enabled": True},
            {"name": "disabled", "priority": 1, "api_key": "k0", "enabled": False},
            {"name": "a", "priority": 1, "api_key": "k1", "enabled": True},
            {"name": "b", "priority": 2, "api_key": "k2", "enabled": True},
        ])
        chain = LLMProviderChain(config_path=path)
        names = [p["name"] for p in chain._providers]
        assert names == ["a", "b", "c"]

    def test_all_disabled_yields_empty(self, tmp_path):
        path = tmp_path / "llm.yaml"
        _write_config(path, [
            {"name": "x", "priority": 1, "api_key": "k", "enabled": False},
        ])
        chain = LLMProviderChain(config_path=path)
        assert chain._providers == []


class TestUnconfigured:
    def test_missing_config_raises(self, tmp_path):
        # yaml 缺失在构造时即抛错（fail-fast）
        with pytest.raises(LLMConfigurationError):
            LLMProviderChain(config_path=tmp_path / "nope.yaml")

    def test_no_enabled_provider_raises(self, tmp_path):
        path = tmp_path / "llm.yaml"
        _write_config(path, [
            {"name": "x", "priority": 1, "api_key": "k", "enabled": False},
        ])
        chain = LLMProviderChain(config_path=path)
        with pytest.raises(LLMConfigurationError):
            chain.call_sync("prompt", _DemoModel)
        with pytest.raises(LLMConfigurationError):
            chain.call_with_fallback("prompt", _DemoModel)


class TestCallSync:
    def test_single_try_no_fallback(self, tmp_path, monkeypatch):
        chain, _ = _make_chain(tmp_path)
        called: list[str] = []

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            called.append(provider["name"])
            raise LLMTimeoutError("超时")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        with pytest.raises(LLMTimeoutError):
            chain.call_sync("prompt", _DemoModel)
        # 同步路由只尝试主 provider，不切换备 provider
        assert called == ["primary"]


class TestCallWithFallback:
    def test_primary_success_stops_chain(self, tmp_path, monkeypatch):
        chain, _ = _make_chain(tmp_path)
        called: list[str] = []

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            called.append(provider["name"])
            return _DemoModel(value="ok")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        result = chain.call_with_fallback("prompt", _DemoModel)
        assert result.value == "ok"
        assert called == ["primary"]

    def test_primary_failure_switches_to_backup(self, tmp_path, monkeypatch):
        chain, _ = _make_chain(tmp_path)
        called: list[str] = []

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            called.append(provider["name"])
            if provider["name"] == "primary":
                raise LLMTimeoutError("主 provider 超时")
            return _DemoModel(value="from-backup")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        result = chain.call_with_fallback("prompt", _DemoModel)
        assert called == ["primary", "backup"]
        assert result.value == "from-backup"

    def test_all_failed_aggregates_errors(self, tmp_path, monkeypatch):
        chain, _ = _make_chain(tmp_path)

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            raise LLMExtractionError(f"{provider['name']} 挂了")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        with pytest.raises(LLMExtractionError) as exc_info:
            chain.call_with_fallback("prompt", _DemoModel)
        msg = str(exc_info.value)
        assert "所有 provider 均失败" in msg
        assert "primary 挂了" in msg
        assert "backup 挂了" in msg
