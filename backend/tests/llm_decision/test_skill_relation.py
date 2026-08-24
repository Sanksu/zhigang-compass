"""技能关系 LLM 决策器测试（PR6：关系类型/方向 + 硬门 + 先修环判定）。"""

from app.services.extraction.llm_provider import LLMExtractionError
from app.services.llm_decision import TIER_BLOCKED, TIER_R2
from app.services.llm_decision.skill_relation import (
    REL_ALTERNATIVE,
    REL_BELONGS,
    REL_NONE,
    REL_PREREQUISITE,
    SkillRelationDecision,
    build_skill_relation_prompt,
    decide_skill_relation,
    prerequisite_cycle_would_create,
    skill_relation_gate,
    tier_for_relation_decision,
)

_KNOWN = {"Java", "Spring", "Python", "Docker"}


def _dec(relation=REL_PREREQUISITE, direction="a_to_b", conf=0.9):
    return SkillRelationDecision(relation=relation, direction=direction, confidence=conf)


class TestRelationSchema:
    def test_invalid_relation_rejected(self):
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SkillRelationDecision(relation="SIMILAR_TO", direction="a_to_b")

    def test_prerequisite_forced_a_to_b(self):
        d = SkillRelationDecision(relation=REL_PREREQUISITE, direction="symmetric")
        assert d.direction == "a_to_b"

    def test_none_resets_direction(self):
        d = SkillRelationDecision(relation=REL_NONE, direction="symmetric")
        assert d.direction == "a_to_b"


class TestRelationGate:
    def test_none_passes(self):
        assert skill_relation_gate(_dec(relation=REL_NONE), "Java", "Spring", _KNOWN) == (True, "")

    def test_self_reference_blocked(self):
        ok, reason = skill_relation_gate(_dec(), "Java", "Java", _KNOWN)
        assert not ok
        assert "自指" in reason

    def test_unknown_target_blocked(self):
        ok, reason = skill_relation_gate(_dec(), "Java", "量子烹饪学", _KNOWN)
        assert not ok
        assert "虚构节点" in reason

    def test_valid_prerequisite_passes(self):
        assert skill_relation_gate(_dec(REL_PREREQUISITE), "Java", "Spring", _KNOWN) == (True, "")

    def test_belongs_to_a_to_b_required(self):
        d = SkillRelationDecision(relation=REL_BELONGS, direction="a_to_b")
        assert skill_relation_gate(d, "Spring", "Java", _KNOWN) == (True, "")

    def test_alternative_symmetric_ok(self):
        d = SkillRelationDecision(relation=REL_ALTERNATIVE, direction="symmetric")
        assert skill_relation_gate(d, "Java", "Python", _KNOWN) == (True, "")


class TestCycleDetection:
    def test_no_cycle_flat(self):
        # Java→Spring 新增边：Spring 的父不含 Java → 无环
        assert prerequisite_cycle_would_create(
            {"Spring": {"Java"}}, "Java", "Spring",
        ) is False

    def test_direct_cycle_detected(self):
        # 已有 Java→Spring（Spring 的父是 Java），新增 Spring→Java 即成环
        assert prerequisite_cycle_would_create(
            {"Spring": {"Java"}}, "Spring", "Java",
        ) is True

    def test_transitive_cycle_detected(self):
        # Java→Spring→Docker；新增 Docker→Java 经传递成环
        assert prerequisite_cycle_would_create(
            {"Spring": {"Java"}, "Docker": {"Spring"}}, "Docker", "Java",
        ) is True

    def test_self_edge_is_cycle(self):
        assert prerequisite_cycle_would_create({"Java": set()}, "Java", "Java") is True

    def test_unrelated_path_not_cycle(self):
        assert prerequisite_cycle_would_create({"Spring": {"Java"}}, "Python", "Docker") is False


class TestPromptAndDecide:
    def test_prompt_carries_evidence(self):
        prompt = build_skill_relation_prompt(
            "Java", "Spring", [{"position": "Java 后端", "count": 5}],
        )
        assert "Java" in prompt
        assert "Spring" in prompt
        assert "Java 后端(5)" in prompt or "Java 后端" in prompt

    def test_decide_parses_valid_output(self):
        sentinel = _dec(REL_PREREQUISITE)

        class _FakeLLM:
            def extract_structured(self, prompt, model, **kwargs):
                return sentinel

        d = decide_skill_relation("Java", "Spring", [{"position": "p", "count": 1}], _FakeLLM())
        assert d is sentinel

    def test_decide_none_on_failure_or_missing_input(self):
        class _BoomLLM:
            def extract_structured(self, prompt, model, **kwargs):
                raise LLMExtractionError("provider 全挂")

        assert decide_skill_relation("Java", "Spring", [], _BoomLLM()) is None
        assert decide_skill_relation("", "Spring", [], object()) is None
        assert decide_skill_relation("Java", "", [], object()) is None
        assert decide_skill_relation("Java", "Spring", [], None) is None

    def test_tier_mapping_always_r2(self):
        assert tier_for_relation_decision(_dec(REL_PREREQUISITE), gate_ok=True)[0] == TIER_R2
        assert tier_for_relation_decision(_dec(REL_PREREQUISITE), gate_ok=False)[0] == TIER_BLOCKED

class TestPromptCalibration:
    """校准 r1：先修判据锚点（防无证据下保守 NONE）。"""

    def test_prompt_contains_prerequisite_criteria(self):
        from app.services.llm_decision.skill_relation import build_skill_relation_prompt

        prompt = build_skill_relation_prompt("Java", "Spring", [])
        assert "前置先修" in prompt
        assert "不要因缺少共现证据而保守答 NONE" in prompt
        assert "子领域" in prompt  # BELONGS_TO 判据
