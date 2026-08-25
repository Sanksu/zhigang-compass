"""名称归一提议脚本测试（PR3 c：编排语义 + R2 档位 + LLM 未配置跳过）。

不触库：验证 propose() 的输入编排与 status/risk_tier 语义，以及
LLMConfigurationError → skipped 分支。判定逻辑复用既有决策器，此处只验证
proposal/R2 的编排层契约。
"""

from app.core import runtime_config
from app.services.extraction.llm_provider import LLMConfigurationError
from app.services.llm_decision import DOMAIN_POSITION_NORMALIZE, DOMAIN_SKILL_NORMALIZE
from scripts.propose_name_normalization import RISK_TIER, _input_hash


class TestRiskTier:
    def test_normalization_is_r2(self):
        """名称归一 = 图 rename/merge = R2 高风险，绝不 auto-apply。"""
        assert RISK_TIER == "R2"


class TestInputHash:
    def test_hash_is_deterministic(self):
        assert _input_hash("skill", "Java") == _input_hash("skill", "Java")
        assert _input_hash("skill", "Java") != _input_hash("skill", "Python")


class TestProposeSkipPath:
    def test_llm_unconfigured_skips(self, monkeypatch):
        """LLM 未配置 → propose() 返回 skipped，不抛异常。"""
        from scripts import propose_name_normalization

        class _BadChain:
            def __init__(self):
                raise LLMConfigurationError("no providers")

        monkeypatch.setattr("app.services.extraction.llm_provider.LLMProviderChain", _BadChain)
        summary = propose_name_normalization.propose(limit=5, domain="all")
        assert summary["status"] == "skipped"
        assert "reason" in summary


class TestDomainConstants:
    def test_domains_match_envelope(self):
        assert DOMAIN_POSITION_NORMALIZE == "position_normalize"
        assert DOMAIN_SKILL_NORMALIZE == "skill_normalize"


class TestShadowFlagDefaultsOff:
    def test_runtime_flag_off_by_default(self):
        """proposal 路径默认关闭（脚本手动跑，非自动）：flag 默认 False。"""
        assert runtime_config.get("name_normalization_shadow_enabled", False) is False
