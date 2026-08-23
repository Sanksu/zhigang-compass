"""LLM api_key 环境变量解析测试（负责人拍板 2026-08-23：key 走 env 不落盘）。

- _resolve_api_key：显式明文优先 → api_key_env 环境变量 → 空
- _call_provider：env 未设置时报 LLMConfigurationError（含 env 提示）
- 配置链路：api_key_env 校验/持久化；显式配 env 后空 key 不再回捞旧明文
"""

import pytest

from app.api.v1.admin_routes.config import (
    LlmConfigIn,
    load_llm_config,
    save_llm_config,
    validate_providers,
)
from app.services.extraction import llm_provider as llm_provider_module
from app.services.extraction.llm_provider import (
    LLMConfigurationError,
    LLMProviderChain,
    _resolve_api_key,
)


class TestResolveApiKey:
    def test_explicit_plaintext_wins(self, monkeypatch):
        monkeypatch.setenv("SOME_KEY", "env-secret")
        provider = {"api_key": "plain-key", "api_key_env": "SOME_KEY"}
        assert _resolve_api_key(provider) == "plain-key"

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "env-secret")
        provider = {"api_key": "", "api_key_env": "DEEPSEEK_API_KEY"}
        assert _resolve_api_key(provider) == "env-secret"

    def test_env_missing_returns_empty(self, monkeypatch):
        monkeypatch.delenv("NOT_SET_VAR", raising=False)
        provider = {"api_key": "", "api_key_env": "NOT_SET_VAR"}
        assert _resolve_api_key(provider) == ""

    def test_nothing_configured_returns_empty(self):
        assert _resolve_api_key({"name": "x"}) == ""

    def test_whitespace_only_explicit_falls_to_env(self, monkeypatch):
        monkeypatch.setenv("K", "v")
        assert _resolve_api_key({"api_key": "   ", "api_key_env": "K"}) == "v"


def _chain(tmp_path, providers):
    import yaml

    path = tmp_path / "llm.yaml"
    path.write_text(
        yaml.safe_dump({"providers": providers}, allow_unicode=True),
        encoding="utf-8",
    )
    return LLMProviderChain(config_path=path)


class TestCallProviderEnvResolution:
    def test_env_unset_raises_config_error_with_hint(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MISSING_KEY", raising=False)
        chain = _chain(tmp_path, [
            {"name": "primary", "priority": 1, "base_url": "https://a.com",
             "model": "m", "enabled": True, "api_key_env": "MISSING_KEY"},
        ])
        with pytest.raises(LLMConfigurationError) as exc_info:
            chain.call_sync("p", llm_provider_module.BaseModel)
        assert "MISSING_KEY" in str(exc_info.value)

    def test_env_set_reaches_client_builder(self, tmp_path, monkeypatch):
        from pydantic import BaseModel as _Base

        captured = {}

        def fake_build(provider, timeout):
            captured["api_key"] = _resolve_api_key(provider)
            # 返回缺 chat 属性的桩 → create 调用抛错走异常包装链（不影响断言）
            return object()

        monkeypatch.setattr(llm_provider_module, "_build_client", fake_build)
        monkeypatch.setenv("PRESENT_KEY", "env-secret")
        chain = _chain(tmp_path, [
            {"name": "primary", "priority": 1, "base_url": "https://a.com",
             "model": "m", "enabled": True, "api_key_env": "PRESENT_KEY"},
        ])

        class _Demo(_Base):
            value: str = "ok"

        with pytest.raises(llm_provider_module.LLMExtractionError):
            # 桩对象不可调用 → 包装异常：说明 api_key 校验已通过
            # （否则会先抛 LLMConfigurationError，走不到 create）
            chain.call_sync("p", _Demo)
        assert captured["api_key"] == "env-secret"


class TestConfigChainApiKeyEnv:
    def test_validate_accepts_valid_env_name(self):
        err = validate_providers([
            {"name": "a", "priority": 1, "base_url": "https://a.com",
             "model": "m", "enabled": True, "api_key_env": "MY_API_KEY"},
        ])
        assert err is None

    def test_validate_rejects_invalid_env_name(self):
        err = validate_providers([
            {"name": "a", "priority": 1, "base_url": "https://a.com",
             "model": "m", "enabled": True, "api_key_env": "9BAD NAME"},
        ])
        assert err is not None and "api_key_env" in err

    def test_model_accepts_and_persists_env_field(self, tmp_path):
        path = tmp_path / "llm_providers.yaml"
        path.write_text("providers: []\n", encoding="utf-8")
        cfg = LlmConfigIn.model_validate({
            "providers": [{
                "name": "deepseek", "priority": 1,
                "base_url": "https://api.deepseek.com",
                "model": "m", "enabled": True,
                "api_key": "", "api_key_env": "DEEPSEEK_API_KEY",
            }],
        })
        saved = save_llm_config(path, [p.model_dump(exclude_none=True) for p in cfg.providers])
        stored = load_llm_config(path)["providers"][0]
        assert saved["providers"][0]["api_key"] == ""
        assert stored["api_key_env"] == "DEEPSEEK_API_KEY"

    def test_empty_key_without_env_keeps_old_value(self, tmp_path):
        """既有语义回归：留空且无 env → 保持旧明文。"""
        path = tmp_path / "llm_providers.yaml"
        path.write_text(
            "providers:\n"
            "  - name: a\n    priority: 1\n    base_url: https://a.com\n"
            "    api_key: sk-old\n    model: m\n    enabled: true\n",
            encoding="utf-8",
        )
        save_llm_config(path, [{
            "name": "a", "priority": 1, "base_url": "https://a.com",
            "model": "m", "enabled": True, "api_key": "",
        }])
        assert load_llm_config(path)["providers"][0]["api_key"] == "sk-old"

    def test_empty_key_with_env_does_not_resurrect_old_plaintext(self, tmp_path):
        """迁移语义：配了 env 后空 key 存空串，旧明文不再回捞。"""
        path = tmp_path / "llm_providers.yaml"
        path.write_text(
            "providers:\n"
            "  - name: a\n    priority: 1\n    base_url: https://a.com\n"
            "    api_key: sk-old-plaintext\n    model: m\n    enabled: true\n",
            encoding="utf-8",
        )
        save_llm_config(path, [{
            "name": "a", "priority": 1, "base_url": "https://a.com",
            "model": "m", "enabled": True, "api_key": "",
            "api_key_env": "NEW_ENV_KEY",
        }])
        stored = load_llm_config(path)["providers"][0]
        assert stored["api_key"] == ""
        assert stored["api_key_env"] == "NEW_ENV_KEY"
