"""真实 LLM 端到端评估测试（TE-M2-04，设计文档 §13.3）。

验证 run_real_eval.py 各函数返回结构稳定且与 evaluate.py 兼容。
使用仓库内真实黄金集（数据确定性，非 mock）。

注意：本模块测试会触发真实 LLM 调用（产生费用），打 @pytest.mark.integration
标记，默认 pytest 运行排除；需显式 `pytest -m integration` 执行（见 pyproject.toml）。
"""


import pytest

from scripts.evaluate import generate_html_report

from tests.evaluate.run_baseline import load_golden_set
from tests.evaluate.run_real_eval import (
    _GOLDEN_JD,
    _MATCH_GOLDEN,
    _RESUME_GOLDEN,
    evaluate_jd_extraction,
    evaluate_matching,
    evaluate_resume_extraction,
)

pytestmark = pytest.mark.integration


class TestEvaluateJdExtraction:
    """JD 真实 LLM 抽取评测测试。"""

    def test_returns_expected_schema(self):
        """JD 真实抽取返回完整结构（含混淆矩阵 + 错误样例）。"""
        golden = load_golden_set(str(_GOLDEN_JD))
        r = evaluate_jd_extraction(golden, limit=5)
        assert r["task"] == "jd"
        assert r["skipped"] is False
        assert r["samples"] > 0
        assert 0.0 <= r["f1"] <= 1.0
        assert 0.0 <= r["precision"] <= 1.0
        assert 0.0 <= r["recall"] <= 1.0
        assert r["target_f1"] == 0.90
        assert "target_met" in r

    def test_has_confusion_matrix(self):
        """设计文档 §13.3：JD 报告含混淆矩阵。"""
        golden = load_golden_set(str(_GOLDEN_JD))
        r = evaluate_jd_extraction(golden, limit=5)
        assert "confusion" in r
        assert all(k in r["confusion"] for k in ("tp", "fp", "fn"))

    def test_has_error_cases(self):
        """设计文档 §13.3：JD 报告含错误样例。"""
        golden = load_golden_set(str(_GOLDEN_JD))
        r = evaluate_jd_extraction(golden, limit=10)
        assert isinstance(r["error_cases"], list)

    def test_method_is_real_llm(self):
        """方法标注为真实 LLM 抽取（区别于基线）。"""
        golden = load_golden_set(str(_GOLDEN_JD))
        r = evaluate_jd_extraction(golden, limit=3)
        assert "LLM" in r["method"]


class TestEvaluateResumeExtraction:
    """简历真实 LLM 抽取评测测试。"""

    def test_returns_expected_schema(self):
        """简历真实抽取返回完整结构。"""
        if not _RESUME_GOLDEN.exists():
            return  # 无黄金集时跳过
        golden = load_golden_set(str(_RESUME_GOLDEN))
        r = evaluate_resume_extraction(golden)
        assert r["task"] == "resume"
        if r.get("skipped"):
            return
        assert r["samples"] > 0
        assert 0.0 <= r["f1"] <= 1.0
        assert r["target_f1"] == 0.90
        assert "target_met" in r

    def test_has_confusion_matrix(self):
        """设计文档 §13.3：简历报告含混淆矩阵。"""
        if not _RESUME_GOLDEN.exists():
            return
        golden = load_golden_set(str(_RESUME_GOLDEN))
        r = evaluate_resume_extraction(golden)
        if r.get("skipped"):
            return
        assert "confusion" in r
        assert all(k in r["confusion"] for k in ("tp", "fp", "fn"))


class TestEvaluateMatching:
    """人岗匹配端到端评测测试。"""

    def test_returns_expected_schema(self):
        """匹配评测返回完整结构（含 Top-3 + 混淆矩阵 + 错误样例）。"""
        if not _MATCH_GOLDEN.exists():
            return
        golden = load_golden_set(str(_MATCH_GOLDEN))
        r = evaluate_matching(golden)
        assert r["task"] == "match"
        assert r["skipped"] is False
        assert 0.0 <= r["spearman"] <= 1.0
        assert 0.0 <= r["accuracy"] <= 1.0
        assert r["target_accuracy"] == 0.90
        assert "target_met" in r

    def test_has_top3_and_confusion(self):
        """设计文档 §9.6/§13.3：匹配报告含 Top-3 + 混淆矩阵。"""
        if not _MATCH_GOLDEN.exists():
            return
        golden = load_golden_set(str(_MATCH_GOLDEN))
        r = evaluate_matching(golden)
        assert "top3_accuracy" in r
        assert "top3_samples" in r
        assert "confusion" in r
        assert all(k in r["confusion"] for k in ("tp", "fp", "tn", "fn"))
        assert isinstance(r["error_cases"], list)


class TestHtmlReportCompatibility:
    """验证 run_real_eval 输出兼容 generate_html_report()。"""

    def test_jd_result_renders_in_html(self):
        """JD 结果可被 generate_html_report 正确渲染。"""
        golden = load_golden_set(str(_GOLDEN_JD))
        r = evaluate_jd_extraction(golden, limit=3)
        report = {
            "generated_at": "20260807_0000",
            "target": "test",
            "results": [r],
        }
        html = generate_html_report(report)
        assert "JD 解析评测详情" in html
        assert "混淆矩阵" in html

    def test_match_result_renders_in_html(self):
        """Match 结果可被 generate_html_report 正确渲染。"""
        if not _MATCH_GOLDEN.exists():
            return
        golden = load_golden_set(str(_MATCH_GOLDEN))
        r = evaluate_matching(golden)
        report = {
            "generated_at": "20260807_0000",
            "target": "test",
            "results": [r],
        }
        html = generate_html_report(report)
        assert "人岗匹配评测详情" in html
        assert "Top-3" in html

    def test_full_report_renders(self):
        """三项结果合并后可生成完整 HTML 报告。"""
        golden_jd = load_golden_set(str(_GOLDEN_JD))
        jd_r = evaluate_jd_extraction(golden_jd, limit=3)

        results: list[dict] = [jd_r]

        # resume（如果有黄金集）
        if _RESUME_GOLDEN.exists():
            resume_golden = load_golden_set(str(_RESUME_GOLDEN))
            resume_r = evaluate_resume_extraction(resume_golden)
            if not resume_r.get("skipped"):
                results.append(resume_r)

        # match（如果有黄金集）
        if _MATCH_GOLDEN.exists():
            match_golden = load_golden_set(str(_MATCH_GOLDEN))
            match_r = evaluate_matching(match_golden)
            results.append(match_r)

        report = {
            "generated_at": "20260807_0000",
            "target": "三项准确率 ≥ 90%（设计文档 §13.3）",
            "results": results,
        }
        html = generate_html_report(report)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html
        assert "总览" in html
