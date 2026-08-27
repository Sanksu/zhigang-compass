"""LLM 配置管理端点安全加固测试（Pydantic 强类型 + 写锁 + 审计留痕）。

- LlmConfigIn：形状/类型错误与业务规则违规（重复 name/priority、坏 base_url）
  均在模型层拒绝（全局处理器映射 422/4000）
- PUT 路由：保存成功后写 AuditLog，detail 绝不含 api_key；
  掩码/空 key 保持原值，明文才更新
- save_llm_config 持锁串行化（纯函数行为由 test_llm_config.py 回归）
"""

import asyncio
import pytest
from pydantic import ValidationError

from app.api.v1.admin_routes.config import (
    LlmConfigIn,
    LlmProviderIn,
    load_llm_config,
    update_llm_config,
)


def _provider(**overrides) -> dict:
    base = {
        "name": "deepseek", "priority": 1,
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "", "model": "deepseek-v4-flash", "enabled": True,
    }
    base.update(overrides)
    return base


class _FakeDB:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


_YAML_SEED = (
    "providers:\n"
    "  - name: deepseek\n"
    "    priority: 1\n"
    "    base_url: https://api.deepseek.com/v1\n"
    "    api_key: sk-old-secret\n"
    "    model: m1\n"
    "    enabled: true\n"
)


class TestLlmConfigInValidation:
    def test_valid_payload_accepted(self):
        cfg = LlmConfigIn(providers=[_provider(), _provider(name="spark", priority=2)])
        assert [p.name for p in cfg.providers] == ["deepseek", "spark"]

    def test_empty_providers_rejected(self):
        with pytest.raises(ValidationError):
            LlmConfigIn(providers=[])

    def test_duplicate_name_rejected(self):
        with pytest.raises(ValidationError):
            LlmConfigIn(providers=[_provider(), _provider(model="m2")])

    def test_duplicate_priority_rejected(self):
        with pytest.raises(ValidationError):
            LlmConfigIn(providers=[_provider(), _provider(name="spark")])

    def test_bad_base_url_rejected(self):
        with pytest.raises(ValidationError):
            LlmConfigIn(providers=[_provider(base_url="api.deepseek.com/v1")])

    def test_nonpositive_priority_rejected(self):
        with pytest.raises(ValidationError):
            LlmConfigIn(providers=[_provider(priority=0)])

    def test_unsafe_name_rejected(self):
        with pytest.raises(ValidationError):
            LlmConfigIn(providers=[_provider(name="bad name!")])

    def test_missing_required_field_rejected(self):
        bad = _provider()
        bad.pop("model")
        with pytest.raises(ValidationError):
            LlmConfigIn(providers=[bad])


class TestUpdateLlmConfigRoute:
    def _seed(self, tmp_path, monkeypatch):
        path = tmp_path / "llm_providers.yaml"
        path.write_text(_YAML_SEED, encoding="utf-8")
        monkeypatch.setattr("app.api.v1.admin_routes.config._LLM_CONFIG_PATH", path)
        return path

    def test_success_writes_audit_without_secret(self, tmp_path, monkeypatch):
        path = self._seed(tmp_path, monkeypatch)
        db = _FakeDB()
        req = LlmConfigIn(providers=[_provider(api_key="****secret")])  # 掩码 → 保原值

        resp = asyncio.run(update_llm_config(req, db=db, current_user={"sub": "0356249f-9b04-47a3-a307-af6e7883f084"}))

        assert resp.code == 0
        # 文件已写回且旧 key 保留
        saved = load_llm_config(path)
        assert saved["providers"][0]["api_key"] == "sk-old-secret"
        # 审计已落：detail 无任何 api_key 明文/掩码值
        assert len(db.added) == 1
        audit = db.added[0]
        assert audit.action == "admin.llm_config.update"
        assert audit.user_id == "0356249f-9b04-47a3-a307-af6e7883f084"
        detail_str = str(audit.detail)
        assert "sk-old-secret" not in detail_str
        assert "****secret" not in detail_str
        entry = audit.detail["providers"][0]
        assert entry["name"] == "deepseek"
        assert entry["key_updated"] is False
        assert "api_key" not in entry

    def test_plain_key_updates_and_flagged_in_audit(self, tmp_path, monkeypatch):
        path = self._seed(tmp_path, monkeypatch)
        db = _FakeDB()
        req = LlmConfigIn(providers=[_provider(api_key="sk-new-plain")])

        resp = asyncio.run(update_llm_config(req, db=db, current_user={"sub": "0356249f-9b04-47a3-a307-af6e7883f084"}))

        assert resp.code == 0
        assert load_llm_config(path)["providers"][0]["api_key"] == "sk-new-plain"
        entry = db.added[0].detail["providers"][0]
        assert entry["key_updated"] is True
        assert "sk-new-plain" not in str(db.added[0].detail)

    def test_response_masks_keys(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        db = _FakeDB()
        req = LlmConfigIn(providers=[_provider(api_key="sk-new-plain")])

        resp = asyncio.run(update_llm_config(req, db=db, current_user={"sub": "0356249f-9b04-47a3-a307-af6e7883f084"}))

        assert resp.data["providers"][0]["api_key"].endswith("lain")
        assert "*" in resp.data["providers"][0]["api_key"]

    def test_extra_body_roundtrip(self, tmp_path, monkeypatch):
        path = self._seed(tmp_path, monkeypatch)
        db = _FakeDB()
        extra = {"thinking": {"type": "disabled"}}
        req = LlmConfigIn(providers=[_provider(extra_body=extra)])

        asyncio.run(update_llm_config(req, db=db, current_user={"sub": "0356249f-9b04-47a3-a307-af6e7883f084"}))

        assert load_llm_config(path)["providers"][0]["extra_body"] == extra


class TestConcurrentSaveSerialization:
    def test_lock_is_reentrant_safe_for_sequential_saves(self, tmp_path, monkeypatch):
        """同进程顺序保存（测试/脚本路径）不受锁阻塞。"""
        from app.api.v1.admin_routes.config import save_llm_config

        path = tmp_path / "llm_providers.yaml"
        path.write_text(_YAML_SEED, encoding="utf-8")
        save_llm_config(path, [_provider()])
        save_llm_config(path, [_provider(model="m2")])
        assert load_llm_config(path)["providers"][0]["model"] == "m2"


class TestLlmProviderInDefaults:
    def test_defaults_match_contract(self):
        p = LlmProviderIn(**{k: v for k, v in _provider().items() if k not in ("enabled",)})
        assert p.enabled is True
        assert p.supports_function_calling is True
        assert p.api_key == ""
        assert p.extra_body is None
