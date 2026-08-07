"""准确率评测脚本测试（AL-M4-04，设计文档 §13.3）。

使用仓库内真实黄金集验证脚本可运行且输出结构稳定（数据确定性，非 mock）。
"""

from scripts.evaluate import (
    _top3_accuracy,
    eval_jd,
    eval_match,
    eval_resume,
    generate_html_report,
)


class TestEvalJd:
    def test_returns_expected_schema(self):
        r = eval_jd()
        assert r["task"] == "jd"
        assert r["skipped"] is False
        assert r["samples"] > 0
        assert 0.0 <= r["f1"] <= 1.0
        assert 0.0 <= r["precision"] <= 1.0
        assert 0.0 <= r["recall"] <= 1.0
        assert r["target_f1"] == 0.90
        assert "target_met" in r

    def test_jd_has_confusion_and_error_cases(self):
        """设计文档 §13.3：JD 报告含混淆矩阵 + 错误分析。"""
        r = eval_jd()
        assert "confusion" in r
        assert all(k in r["confusion"] for k in ("tp", "fp", "fn"))
        assert isinstance(r["error_cases"], list)


class TestEvalResume:
    def test_returns_expected_schema(self):
        """简历黄金集已交付（AL-M5-02）：resume 项真实评测并返回结构。

        无 LLM 配置环境（CI）走规则兜底，不崩溃、不伪造结果。
        """
        r = eval_resume()
        assert r["task"] == "resume"
        assert r["skipped"] is False
        assert r["samples"] > 0
        assert 0.0 <= r["f1"] <= 1.0
        assert 0.0 <= r["precision"] <= 1.0
        assert 0.0 <= r["recall"] <= 1.0
        assert r["target_f1"] == 0.90
        assert "target_met" in r

    def test_resume_has_confusion(self):
        """设计文档 §13.3：简历报告含混淆矩阵。"""
        r = eval_resume()
        assert "confusion" in r
        assert all(k in r["confusion"] for k in ("tp", "fp", "fn"))


class TestEvalMatch:
    def test_rule_baseline_returns_expected_schema(self):
        r = eval_match(semantic=False)
        assert r["task"] == "match"
        assert r["skipped"] is False
        assert 0.0 <= r["spearman"] <= 1.0
        assert 0.0 <= r["accuracy"] <= 1.0
        assert r["target_accuracy"] == 0.90
        assert "target_met" in r

    def test_match_has_top3_and_confusion(self):
        """设计文档 §9.6/§13.3：匹配报告含 Top-3 准确率 + 混淆矩阵 + 错误样例。"""
        r = eval_match(semantic=False)
        assert "top3_accuracy" in r
        assert "top3_samples" in r
        assert "confusion" in r
        assert all(k in r["confusion"] for k in ("tp", "fp", "tn", "fn"))
        assert isinstance(r["error_cases"], list)

    def test_jd_golden_set_has_positive_samples(self):
        """黄金集样本足够（字段级评测有意义的前提）。"""
        from tests.evaluate.run_baseline import load_golden_set

        from scripts.evaluate import _JD_GOLDEN

        items = load_golden_set(str(_JD_GOLDEN))
        assert len(items) >= 50


class TestTop3Accuracy:
    """Top-3 推荐准确率单元测试（设计文档 §9.6/§13.3）。"""

    def test_perfect_ranking(self):
        """所有正样本都在 Top-3 → accuracy = 1.0。"""
        pairs = [
            {"candidate_skills": ["Python"], "position_id": "p1", "label": 1},
            {"candidate_skills": ["Python"], "position_id": "p2", "label": 0},
            {"candidate_skills": ["Python"], "position_id": "p3", "label": 0},
            {"candidate_skills": ["Python"], "position_id": "p4", "label": 0},
        ]
        # 正样本得分最高
        scores = [0.9, 0.3, 0.2, 0.1]
        acc, n = _top3_accuracy(pairs, scores)
        assert acc == 1.0
        assert n == 1

    def test_positive_outside_top3(self):
        """正样本排在 Top-3 之外 → accuracy = 0.0。"""
        pairs = [
            {"candidate_skills": ["Python"], "position_id": "p1", "label": 0},
            {"candidate_skills": ["Python"], "position_id": "p2", "label": 0},
            {"candidate_skills": ["Python"], "position_id": "p3", "label": 0},
            {"candidate_skills": ["Python"], "position_id": "p4", "label": 1},
        ]
        # 正样本得分最低
        scores = [0.9, 0.8, 0.7, 0.1]
        acc, n = _top3_accuracy(pairs, scores)
        assert acc == 0.0
        assert n == 1

    def test_no_eligible_candidates(self):
        """所有候选人对数 < 3 → 无合格候选人，返回 (None, 0)。"""
        pairs = [
            {"candidate_skills": ["Python"], "position_id": "p1", "label": 1},
            {"candidate_skills": ["Python"], "position_id": "p2", "label": 0},
        ]
        scores = [0.9, 0.1]
        acc, n = _top3_accuracy(pairs, scores)
        assert acc is None
        assert n == 0

    def test_candidate_without_positives_skipped(self):
        """候选人无正样本 → 跳过，不计入分母。"""
        pairs = [
            {"candidate_skills": ["Python"], "position_id": "p1", "label": 0},
            {"candidate_skills": ["Python"], "position_id": "p2", "label": 0},
            {"candidate_skills": ["Python"], "position_id": "p3", "label": 0},
        ]
        scores = [0.9, 0.5, 0.1]
        acc, n = _top3_accuracy(pairs, scores)
        assert acc is None
        assert n == 0

    def test_multiple_candidates_mixed(self):
        """多个候选人混合情况：1 个命中 + 1 个未命中 → accuracy = 0.5。"""
        pairs = [
            # 候选人 A：正样本在 Top-3（命中）
            {"candidate_skills": ["A"], "position_id": "a1", "label": 1},
            {"candidate_skills": ["A"], "position_id": "a2", "label": 0},
            {"candidate_skills": ["A"], "position_id": "a3", "label": 0},
            {"candidate_skills": ["A"], "position_id": "a4", "label": 0},
            # 候选人 B：正样本不在 Top-3（未命中）
            {"candidate_skills": ["B"], "position_id": "b1", "label": 0},
            {"candidate_skills": ["B"], "position_id": "b2", "label": 0},
            {"candidate_skills": ["B"], "position_id": "b3", "label": 0},
            {"candidate_skills": ["B"], "position_id": "b4", "label": 1},
        ]
        scores = [
            0.9, 0.3, 0.2, 0.1,  # A: positive at top
            0.9, 0.8, 0.7, 0.1,  # B: positive at bottom
        ]
        acc, n = _top3_accuracy(pairs, scores)
        assert acc == 0.5
        assert n == 2


class TestHtmlReport:
    """HTML 评测报告测试（设计文档 §13.3：分项得分+错误分析+混淆矩阵）。"""

    def _make_full_report(self) -> dict:
        """构造三项全跑的报告（含 Top-3 和错误样例）。"""
        return {
            "generated_at": "20260806_1200",
            "target": "三项准确率 ≥ 90%（设计文档 §13.3）",
            "results": [
                {
                    "task": "jd",
                    "skipped": False,
                    "method": "关键词基线（无 LLM，离线）",
                    "samples": 100,
                    "precision": 0.85,
                    "recall": 0.90,
                    "f1": 0.8724,
                    "target_f1": 0.90,
                    "target_met": False,
                    "confusion": {"tp": 200, "fp": 35, "fn": 22},
                    "error_cases": [
                        {"source_id": "jd_001", "false_positives": ["Docker"], "false_negatives": ["K8s"]},
                    ],
                },
                {
                    "task": "resume",
                    "skipped": False,
                    "method": "真实抽取（LLM + 规则兜底）",
                    "samples": 50,
                    "precision": 0.88,
                    "recall": 0.92,
                    "f1": 0.8998,
                    "target_f1": 0.90,
                    "target_met": False,
                    "confusion": {"tp": 150, "fp": 20, "fn": 13},
                },
                {
                    "task": "match",
                    "skipped": False,
                    "method": "规则匹配（无语义）",
                    "spearman": 0.82,
                    "accuracy": 0.88,
                    "target_accuracy": 0.90,
                    "target_met": False,
                    "top3_accuracy": 0.75,
                    "top3_samples": 8,
                    "confusion": {"tp": 40, "fp": 10, "tn": 35, "fn": 15},
                    "error_cases": [
                        {"position_id": "pos_001", "score": 0.42, "label": 1, "error_type": "FN"},
                    ],
                },
            ],
        }

    def test_full_report_contains_all_sections(self):
        """完整报告含三项详情 + 总览。"""
        report = self._make_full_report()
        html = generate_html_report(report)
        assert "JD 解析评测详情" in html
        assert "简历提取评测详情" in html
        assert "人岗匹配评测详情" in html
        assert "总览" in html

    def test_report_contains_confusion_matrix(self):
        """设计文档 §13.3：报告含混淆矩阵。"""
        report = self._make_full_report()
        html = generate_html_report(report)
        assert "混淆矩阵" in html
        assert "TP" in html
        assert "FP" in html
        assert "FN" in html
        assert "TN" in html

    def test_report_contains_error_analysis(self):
        """设计文档 §13.3：报告含错误分析。"""
        report = self._make_full_report()
        html = generate_html_report(report)
        assert "错误样例" in html
        assert "jd_001" in html
        assert "FN" in html

    def test_report_contains_top3_accuracy(self):
        """设计文档 §9.6：报告含 Top-3 推荐准确率。"""
        report = self._make_full_report()
        html = generate_html_report(report)
        assert "Top-3" in html
        assert "0.7500" in html

    def test_report_contains_badges(self):
        """达标/未达标徽章存在。"""
        report = self._make_full_report()
        html = generate_html_report(report)
        assert "未达标" in html

    def test_skipped_task_renders_skip_badge(self):
        """跳过的任务显示"跳过"徽章。"""
        report = {
            "generated_at": "20260806_1200",
            "target": "test",
            "results": [
                {"task": "jd", "skipped": True, "reason": "黄金集缺失"},
            ],
        }
        html = generate_html_report(report)
        assert "跳过" in html
        assert "黄金集缺失" in html

    def test_match_top3_none_renders_na(self):
        """match 的 top3_accuracy 为 None 时显示 N/A。"""
        report = self._make_full_report()
        report["results"][2]["top3_accuracy"] = None
        html = generate_html_report(report)
        assert "N/A" in html

    def test_html_is_valid_structure(self):
        """HTML 是有效的文档结构。"""
        report = self._make_full_report()
        html = generate_html_report(report)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html
        assert "<html" in html
        assert "<body>" in html
        assert "</body>" in html
