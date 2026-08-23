"""M1 实验抽样纯函数测试（select_experiment_candidates，与线上触发门同口径）。"""

from app.services.extraction.position_review import select_experiment_candidates


def _rows(*items):
    return [{"name": n, "req_count": c} for n, c in items]


class TestSelectExperimentCandidates:
    def test_whitelist_and_stopword_names_filtered(self):
        rows = _rows(("前端开发工程师", 0), ("技术", 0), ("某未知名X9", 0))
        assert select_experiment_candidates(rows) == ["某未知名X9"]

    def test_high_ref_filtered(self):
        rows = _rows(("某未知名X9", 5), ("另一未知Y2", 1))
        assert select_experiment_candidates(rows) == ["另一未知Y2"]

    def test_order_preserved_and_deduped(self):
        rows = _rows(("Alpha岗", 0), ("Beta岗", 0), ("Alpha岗", 0))
        out = select_experiment_candidates(rows)
        assert out == ["Alpha岗", "Beta岗"]

    def test_limit_caps(self):
        rows = _rows(*[(f"未知岗{i}Z", 0) for i in range(10)])
        assert len(select_experiment_candidates(rows, limit=3)) == 3

    def test_short_names_skipped(self):
        rows = _rows(("A", 0), ("AB岗", 0))
        # "A" 过短跳过；"AB岗" 含 ASCII 未命中族 → 候选
        assert select_experiment_candidates(rows) == ["AB岗"]

    def test_empty_rows(self):
        assert select_experiment_candidates([]) == []
