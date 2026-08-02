"""准确率评测脚本测试（AL-M4-04，设计文档 §13.3）。

使用仓库内真实黄金集验证脚本可运行且输出结构稳定（数据确定性，非 mock）。
"""

from scripts.evaluate import eval_jd, eval_match


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


class TestEvalMatch:
    def test_rule_baseline_returns_expected_schema(self):
        r = eval_match(semantic=False)
        assert r["task"] == "match"
        assert r["skipped"] is False
        assert 0.0 <= r["spearman"] <= 1.0
        assert 0.0 <= r["accuracy"] <= 1.0
        assert r["target_accuracy"] == 0.90
        assert "target_met" in r

    def test_jd_golden_set_has_positive_samples(self):
        """黄金集样本足够（字段级评测有意义的前提）。"""
        from tests.evaluate.run_baseline import load_golden_set

        from scripts.evaluate import _JD_GOLDEN

        items = load_golden_set(str(_JD_GOLDEN))
        assert len(items) >= 50
