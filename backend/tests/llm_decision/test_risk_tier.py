"""LLM 决策统一信封：风险路由与决策记录构造（PR1 主仓灰度底座）。"""

import pytest

from app.services import llm_decision as ld


class TestRiskTierFor:
    def test_gate_fail_blocks_every_action(self):
        tier, reason = ld.risk_tier_for(
            ld.DOMAIN_SKILL_CLASSIFY, "add_stopword", gate_ok=False,
            confidence=0.99, impact_nodes=1,
        )
        assert tier == ld.TIER_BLOCKED
        assert reason

    def test_unknown_domain_rejected(self):
        with pytest.raises(ValueError):
            ld.risk_tier_for("not_a_domain", "add_stopword", True, 0.9)

    def test_r0_suggestion_auto_allowed(self):
        tier, reason = ld.risk_tier_for(
            ld.DOMAIN_SKILL_CLASSIFY, "suggest_category", True, 0.75, 100,
        )
        assert tier == ld.TIER_R0
        assert reason == ""

    def test_r1_low_impact_high_confidence(self):
        tier, reason = ld.risk_tier_for(
            ld.DOMAIN_GOVERNANCE, "add_stopword", True, 0.9, impact_nodes=3,
        )
        assert tier == ld.TIER_R1
        assert reason == ""

    def test_r1_low_confidence_downgraded_to_r2(self):
        tier, reason = ld.risk_tier_for(
            ld.DOMAIN_GOVERNANCE, "add_stopword", True, 0.5, impact_nodes=3,
        )
        assert tier == ld.TIER_R2
        assert "置信度" in reason

    def test_r1_high_impact_downgraded_to_r2(self):
        tier, reason = ld.risk_tier_for(
            ld.DOMAIN_GOVERNANCE, "add_stopword", True, 0.9, impact_nodes=200,
        )
        assert tier == ld.TIER_R2
        assert "影响面" in reason

    def test_high_risk_action_always_r2(self):
        tier, reason = ld.risk_tier_for(
            ld.DOMAIN_GOVERNANCE, "remove_stopword", True, 0.99, impact_nodes=1,
        )
        assert tier == ld.TIER_R2
        assert reason

    def test_runtime_config_overrides_defaults(self, monkeypatch):
        from app.core import runtime_config

        monkeypatch.setattr(runtime_config, "_cache", {
            "llm_decision_min_confidence": 0.95,
            "llm_decision_auto_impact_max": 1,
        })
        tier, _ = ld.risk_tier_for(
            ld.DOMAIN_GOVERNANCE, "add_stopword", True, 0.9, impact_nodes=3,
        )
        assert tier == ld.TIER_R2  # 0.9 < 0.95 被更严门限拦截

    def test_relation_action_requires_human(self):
        tier, reason = ld.risk_tier_for(
            ld.DOMAIN_SKILL_RELATION, "add_prerequisite", True, 0.95, impact_nodes=2,
        )
        assert tier == ld.TIER_R2
        assert reason


class TestBuildRecord:
    def test_defaults_shadow_and_clean_payload(self):
        rec = ld.build_record(domain=ld.DOMAIN_JD_EXTRACT, entity_id="jd-1")
        assert rec.status == ld.STATUS_SHADOW
        assert rec.domain == ld.DOMAIN_JD_EXTRACT
        assert rec.evidence_refs == []
        assert rec.structured_output == {}
        assert rec.env == "production"
        assert rec.attempts == 1

    def test_fields_roundtrip(self):
        rec = ld.build_record(
            domain=ld.DOMAIN_SKILL_RELATION,
            entity_type="skill",
            entity_id="sk-9",
            run_id="run-20260824-01",
            input_hash="a" * 64,
            evidence_refs=[{"label": "jd_cooccur", "value": 3}],
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_version="v3",
            schema_version="v1",
            structured_output={"relation": "PREREQUISITE_OF", "direction": "a->b"},
            confidence=0.93,
            gate_result="pass",
            risk_tier=ld.TIER_R2,
            status=ld.STATUS_PROPOSAL,
            duration_ms=1200,
            attempts=2,
            fallback_reason="timeout",
        )
        assert rec.entity_id == "sk-9"
        assert rec.input_hash == "a" * 64
        assert rec.evidence_refs[0]["label"] == "jd_cooccur"
        assert rec.confidence == 0.93
        assert rec.risk_tier == ld.TIER_R2
        assert rec.postprocessed_output is None

    def test_invalid_domain_rejected(self):
        with pytest.raises(ValueError):
            ld.build_record(domain="bogus")

    def test_invalid_status_rejected(self):
        with pytest.raises(ValueError):
            ld.build_record(domain=ld.DOMAIN_JD_EXTRACT, status="nope")

    def test_domain_set_membership(self):
        assert ld.DOMAINS == frozenset({
            "jd_extract", "position_normalize", "skill_normalize",
            "position_classify", "cluster_label", "skill_classify",
            "governance", "skill_relation",
        })