"""LLM-as-judge 交叉验证测试（TE-M2-03，设计文档 §13.3）。

测试核心函数的逻辑正确性，不依赖外部 LLM API（使用 Mock 客户端）。
"""

import json

import pytest
from pydantic import ValidationError

from tests.evaluate.judge_eval import (
    JudgeResult,
    _parse_json,
    compute_agreement,
    judge_jd_extraction,
)


# ---------------------------------------------------------------------------
# compute_agreement — Cohen's Kappa
# ---------------------------------------------------------------------------


class TestComputeAgreement:
    """Cohen's Kappa 一致性计算测试。"""

    def test_perfect_agreement(self):
        """judge 与项目评分完全一致 → Kappa = 1.0。"""
        scores = [
            {"score": 0.9, "project_score": 0.9},
            {"score": 0.8, "project_score": 0.85},
            {"score": 0.3, "project_score": 0.2},
            {"score": 0.1, "project_score": 0.15},
        ]
        kappa = compute_agreement(scores)
        assert kappa == 1.0

    def test_no_agreement(self):
        """judge 与项目评分完全相反 → Kappa < 0。"""
        scores = [
            {"score": 0.9, "project_score": 0.1},
            {"score": 0.1, "project_score": 0.9},
            {"score": 0.8, "project_score": 0.2},
            {"score": 0.2, "project_score": 0.8},
        ]
        kappa = compute_agreement(scores)
        assert kappa < 0.0

    def test_insufficient_samples(self):
        """样本数 < 2 → 返回 0.0（无法计算 Kappa）。"""
        scores = [{"score": 0.9, "project_score": 0.9}]
        kappa = compute_agreement(scores)
        assert kappa == 0.0

    def test_empty_list(self):
        """空列表 → 返回 0.0。"""
        kappa = compute_agreement([])
        assert kappa == 0.0

    def test_all_same_labels_pe_equals_one(self):
        """所有标签相同（pe = 1.0）→ 返回 1.0（避免除零）。"""
        scores = [
            {"score": 0.9, "project_score": 0.9},
            {"score": 0.8, "project_score": 0.9},
            {"score": 0.7, "project_score": 0.9},
        ]
        kappa = compute_agreement(scores)
        assert kappa == 1.0

    def test_partial_agreement_in_range(self):
        """部分一致 → Kappa 在 (0, 1) 区间。

        4 样本：3 一致（2 pass + 1 fail）+ 1 不一致。
        po=0.75, pe=0.5, Kappa=(0.75-0.5)/(1-0.5)=0.5。
        """
        scores = [
            {"score": 0.9, "project_score": 0.9},  # agree (both pass)
            {"score": 0.3, "project_score": 0.3},  # agree (both fail)
            {"score": 0.9, "project_score": 0.9},  # agree (both pass)
            {"score": 0.3, "project_score": 0.9},  # disagree (judge fail, project pass)
        ]
        kappa = compute_agreement(scores)
        assert 0.0 < kappa < 1.0

    def test_threshold_boundary(self):
        """二值化阈值 0.5 边界：score = 0.5 视为合格。"""
        scores = [
            {"score": 0.5, "project_score": 0.5},  # both pass
            {"score": 0.49, "project_score": 0.49},  # both fail
        ]
        kappa = compute_agreement(scores)
        assert kappa == 1.0


# ---------------------------------------------------------------------------
# _parse_json — LLM 返回容错解析
# ---------------------------------------------------------------------------


class TestParseJson:
    """JSON 容错解析测试。"""

    def test_clean_json(self):
        """干净的 JSON 字符串。"""
        raw = '{"score": 0.8, "reasoning": "good", "missing_fields": [], "hallucinated_fields": []}'
        data = _parse_json(raw)
        assert data["score"] == 0.8
        assert data["reasoning"] == "good"

    def test_markdown_wrapped_json(self):
        """Markdown 代码块包裹的 JSON。"""
        raw = '```json\n{"score": 0.7, "reasoning": "ok"}\n```'
        data = _parse_json(raw)
        assert data["score"] == 0.7

    def test_json_embedded_in_text(self):
        """JSON 嵌在文本中（容错提取）。"""
        raw = 'Here is the result:\n{"score": 0.6, "reasoning": "fair"}\nDone.'
        data = _parse_json(raw)
        assert data["score"] == 0.6

    def test_invalid_input_raises(self):
        """非 JSON 输入 → ValueError。"""
        with pytest.raises(ValueError):
            _parse_json("this is not json at all")


# ---------------------------------------------------------------------------
# JudgeResult — Pydantic 强校验
# ---------------------------------------------------------------------------


class TestJudgeResult:
    """JudgeResult Pydantic 模型校验测试。"""

    def test_valid_result(self):
        """合法数据 → 正常创建。"""
        r = JudgeResult(
            score=0.85,
            reasoning="抽取质量良好",
            missing_fields=["Docker"],
            hallucinated_fields=[],
        )
        assert r.score == 0.85
        assert r.missing_fields == ["Docker"]

    def test_score_out_of_range_high(self):
        """score > 1.0 → ValidationError。"""
        with pytest.raises(ValidationError):
            JudgeResult(score=1.5, reasoning="invalid")

    def test_score_out_of_range_low(self):
        """score < 0.0 → ValidationError。"""
        with pytest.raises(ValidationError):
            JudgeResult(score=-0.1, reasoning="invalid")

    def test_missing_required_field(self):
        """缺少 reasoning → ValidationError。"""
        with pytest.raises(ValidationError):
            JudgeResult(score=0.5)

    def test_defaults_for_optional_fields(self):
        """missing_fields / hallucinated_fields 默认空列表。"""
        r = JudgeResult(score=0.5, reasoning="test")
        assert r.missing_fields == []
        assert r.hallucinated_fields == []


# ---------------------------------------------------------------------------
# judge_jd_extraction — Mock LLM 集成
# ---------------------------------------------------------------------------


class _MockJudgeLLM:
    """Mock LLM 客户端，返回预设响应。"""

    def __init__(self, response: str):
        self._response = response

    def chat(self, system: str, user: str) -> str:
        return self._response


class TestJudgeJdExtraction:
    """judge_jd_extraction 函数测试（使用 Mock LLM）。"""

    def test_valid_judge_response(self):
        """LLM 返回合法 JSON → 正常解析返回。"""
        mock_resp = json.dumps({
            "score": 0.85,
            "reasoning": "抽取质量良好，漏抽 1 项",
            "missing_fields": ["Docker"],
            "hallucinated_fields": [],
        })
        judge_llm = _MockJudgeLLM(mock_resp)
        result = judge_jd_extraction(
            pred={"skills": ["Python", "FastAPI"]},
            gold={"gold_skills": ["Python", "FastAPI", "Docker"]},
            judge_llm=judge_llm,
        )
        assert result["score"] == 0.85
        assert result["missing_fields"] == ["Docker"]
        assert result["hallucinated_fields"] == []

    def test_markdown_wrapped_response(self):
        """LLM 返回 Markdown 包裹 JSON → 容错解析。"""
        mock_resp = '```json\n{"score": 0.7, "reasoning": "fair", "missing_fields": [], "hallucinated_fields": ["Rust"]}\n```'
        judge_llm = _MockJudgeLLM(mock_resp)
        result = judge_jd_extraction(
            pred={"skills": ["Python", "Rust"]},
            gold={"gold_skills": ["Python"]},
            judge_llm=judge_llm,
        )
        assert result["score"] == 0.7
        assert result["hallucinated_fields"] == ["Rust"]

    def test_invalid_llm_response_raises(self):
        """LLM 返回非 JSON → ValueError。"""
        judge_llm = _MockJudgeLLM("I cannot evaluate this.")
        with pytest.raises(ValueError):
            judge_jd_extraction(
                pred={"skills": ["Python"]},
                gold={"gold_skills": ["Python"]},
                judge_llm=judge_llm,
            )

    def test_empty_skills(self):
        """空技能列表 → judge 仍能正常工作。"""
        mock_resp = json.dumps({
            "score": 0.0,
            "reasoning": "无技能抽取",
            "missing_fields": ["Python"],
            "hallucinated_fields": [],
        })
        judge_llm = _MockJudgeLLM(mock_resp)
        result = judge_jd_extraction(
            pred={"skills": []},
            gold={"gold_skills": ["Python"]},
            judge_llm=judge_llm,
        )
        assert result["score"] == 0.0
        assert result["missing_fields"] == ["Python"]
