"""岗位分类 LLM 决策器测试（PR4a：清单内强制选择 + 防自创分类硬门）。"""

from app.services.extraction.llm_provider import LLMExtractionError
from app.services.llm_decision import TIER_BLOCKED, TIER_R0
from app.services.llm_decision.position_classify import (
    PositionClassifyDecision,
    build_position_classify_prompt,
    decide_position_classify,
    position_classify_gate,
    tier_for_position_classify,
)

_CATEGORIES = ["AI/机器学习", "云原生/DevOps", "数据", "前端", "后端", "测试"]


class TestPositionClassifyGate:
    def test_category_in_list_passes(self):
        dec = PositionClassifyDecision(category="AI/机器学习", confidence=0.95)
        assert position_classify_gate(dec, _CATEGORIES) == (True, "")

    def test_invented_category_blocked(self):
        dec = PositionClassifyDecision(category="量子计算", confidence=0.99)
        ok, reason = position_classify_gate(dec, _CATEGORIES)
        assert not ok
        assert "自创分类" in reason

    def test_empty_category_blocked(self):
        dec = PositionClassifyDecision.model_construct(category="", confidence=0.5)
        ok, reason = position_classify_gate(dec, _CATEGORIES)
        assert not ok


class TestPositionClassifyPromptAndDecide:
    def test_prompt_carries_evidence(self):
        prompt = build_position_classify_prompt("算法工程师", ["Python", "PyTorch"], _CATEGORIES)
        assert "算法工程师" in prompt
        assert "AI/机器学习" in prompt
        assert "PyTorch" in prompt

    def test_decide_parses_valid_output(self):
        sentinel = PositionClassifyDecision(category="数据", confidence=0.9)

        class _FakeLLM:
            def extract_structured(self, prompt, model, **kwargs):
                return sentinel

        decision = decide_position_classify("数据开发", ["SQL"], _CATEGORIES, _FakeLLM())
        assert decision is sentinel

    def test_decide_none_on_failure_or_missing_input(self):
        class _BoomLLM:
            def extract_structured(self, prompt, model, **kwargs):
                raise LLMExtractionError("provider 全挂")

        assert decide_position_classify("数据开发", ["SQL"], _CATEGORIES, _BoomLLM()) is None
        assert decide_position_classify("", ["SQL"], _CATEGORIES, object()) is None
        assert decide_position_classify("数据开发", ["SQL"], [], object()) is None
        assert decide_position_classify("数据开发", ["SQL"], _CATEGORIES, None) is None

    def test_tier_mapping(self):
        dec = PositionClassifyDecision(category="数据", confidence=0.9)
        assert tier_for_position_classify(dec, gate_ok=True) == (TIER_R0, "")
        assert tier_for_position_classify(dec, gate_ok=False)[0] == TIER_BLOCKED