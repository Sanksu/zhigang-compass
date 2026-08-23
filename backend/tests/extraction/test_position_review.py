"""岗位名 LLM 审查单元测试（《岗位名LLM审查设计方案》决策表全覆盖）。

- should_review：规则拦截/白名单族不审，未知低频才审
- review_position_name：llm 缺失/LLMExtractionError 降级返回 None
- apply_decision：invalid 置空 / 保守保留 / 修正采用 / 修正被拒四分支 + 降级
"""


from app.services.extraction.llm_provider import LLMExtractionError
from app.services.extraction.position_review import (
    PositionReviewResult,
    apply_decision,
    extraction_skills,
    review_position_name,
    should_review,
)


def _result(valid=True, category="standard", standard=None, reason="r"):
    return PositionReviewResult(
        valid=valid, category=category, standard_name=standard, reason=reason,
    )


class _FakeLLM:
    def __init__(self, outcome=None, error=None):
        self._outcome = outcome
        self._error = error
        self.calls = []

    def extract_structured(self, prompt, response_model, system_prompt=None, timeout=None):
        self.calls.append({"timeout": timeout, "model": response_model})
        if self._error is not None:
            raise self._error
        return self._outcome


class TestShouldReview:
    def test_whitelist_family_not_reviewed(self):
        # 规则已分类的标准岗位族不审
        assert should_review("前端开发工程师", frequency=0) is False

    def test_unknown_lowfreq_reviewed(self):
        assert should_review("GTM式碎片", frequency=0) is True
        assert should_review("GTM式碎片", frequency=4) is True

    def test_highfreq_not_reviewed(self):
        assert should_review("某未知新名", frequency=5) is False

    def test_empty_normalized_not_reviewed(self):
        assert should_review("", frequency=0) is False


class TestReviewPositionName:
    def test_llm_none_degrades(self):
        assert review_position_name("X", ["Python"], None) is None

    def test_empty_name_degrades(self):
        llm = _FakeLLM(outcome=_result())
        assert review_position_name("  ", ["Python"], llm) is None
        assert llm.calls == []

    def test_llm_error_degrades_to_none(self):
        llm = _FakeLLM(error=LLMExtractionError("超时"))
        assert review_position_name("某岗位", [], llm) is None

    def test_success_uses_15s_timeout_and_schema(self):
        llm = _FakeLLM(outcome=_result())
        result = review_position_name("某岗位", ["Python", "PyTorch"], llm)
        assert result.valid is True
        assert llm.calls[0]["timeout"] == 15
        assert llm.calls[0]["model"] is PositionReviewResult


class TestApplyDecision:
    def test_none_keeps_original_without_record(self):
        final, record = apply_decision(None, "原名", [])
        assert final == "原名"
        assert record is None

    def test_invalid_blanks_name_with_audit_record(self):
        final, record = apply_decision(
            _result(valid=False, category="abbreviation", reason="产品名"),
            "Salesforce", [],
        )
        assert final == ""
        assert record["valid"] is False
        assert record["original"] == "Salesforce"
        assert record["category"] == "abbreviation"
        assert "reviewed_at" in record

    def test_valid_no_standard_keeps_original(self):
        final, record = apply_decision(_result(), "数据工程专家", ["SQL"])
        assert final == "数据工程专家"
        assert record["valid"] is True
        assert record["standard_name"] is None

    def test_standard_passing_normalize_adopted(self):
        final, record = apply_decision(
            _result(standard="机器视觉算法工程师"), "AI应用",
            ["计算机视觉"],
        )
        assert final == "机器视觉算法工程师"
        assert record["standard_name"] == "机器视觉算法工程师"

    def test_standard_failing_normalize_rejected_only_marked(self):
        # 纯中文非白名单名 normalize 为空串 = 未通过规则校验
        final, record = apply_decision(
            _result(standard="完全不存在的乱码岗位"), "某名称", [],
        )
        assert final == "某名称"
        assert record["standard_name"] is None
        assert record["standard_rejected"] == "完全不存在的乱码岗位"


class TestExtractionSkills:
    def test_merges_skills_and_requirements_dedup_ordered(self):
        class _S:
            def __init__(self, name):
                self.name = name

        class _R:
            def __init__(self, skill_name):
                self.skill_name = skill_name

        out = extraction_skills([_S("Python"), _S("SQL"), None], [_R("Python"), _R("Docker")])
        assert out == ["Python", "SQL", "Docker"]

    def test_empty_inputs(self):
        assert extraction_skills([], []) == []
