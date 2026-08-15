"""Bradley-Terry 匹配权重调优脚本单元测试（AL-M5-02）。

覆盖：
- 同候选组内成对构造（禁止跨候选人交叉）
- BT 拟合收敛（合成可分数据上权重方向正确）
- 可识别维度判定（全 must 黄金集仅 must 可识别）
- 数据不足退化逻辑（不写回 configs）
"""

import pytest

from scripts import tune_match_bt as bt


def _sample_pairs() -> list[dict]:
    """合成黄金集：两个候选组，正负岗位各若干（技能匹配可分）。"""
    return [
        # 候选组 A：会 Python → Python 岗位匹配，Java 岗位不匹配
        {"candidate_skills": ["Python"], "position_skills": ["Python"], "label": 1, "position_id": "jd_a1"},
        {"candidate_skills": ["Python"], "position_skills": ["Python", "Django"], "label": 1, "position_id": "jd_a2"},
        {"candidate_skills": ["Python"], "position_skills": ["Java"], "label": 0, "position_id": "jd_a3"},
        {"candidate_skills": ["Python"], "position_skills": ["Java", "Spring"], "label": 0, "position_id": "jd_a4"},
        # 候选组 B：会 Java → Java 岗位匹配，Python 岗位不匹配
        {"candidate_skills": ["Java"], "position_skills": ["Java"], "label": 1, "position_id": "jd_b1"},
        {"candidate_skills": ["Java"], "position_skills": ["Java", "Spring"], "label": 1, "position_id": "jd_b2"},
        {"candidate_skills": ["Java"], "position_skills": ["Python"], "label": 0, "position_id": "jd_b3"},
        {"candidate_skills": ["Java"], "position_skills": ["Python", "Django"], "label": 0, "position_id": "jd_b4"},
    ]


class TestBuildPairwise:
    def test_pairwise_within_candidate_group(self):
        """成对仅限同候选组内（正×负），跨组不交叉。"""
        records = [("A", [1.0, 1.0, 1.0], 1), ("A", [0.0, 1.0, 1.0], 0), ("B", [1.0, 1.0, 1.0], 1), ("B", [0.0, 1.0, 1.0], 0)]
        pairs = bt.build_pairwise(records)
        # A 组 1×1 + B 组 1×1 = 2 对（若跨组交叉会是 4 对）
        assert len(pairs) == 2

    def test_empty_pairs(self):
        assert bt.build_pairwise([]) == []


class TestFitBT:
    def test_identifiable_dims_must_only(self):
        """全 must 黄金集（nice/exp 恒定）仅 must 维度可识别。"""
        pairs = _sample_pairs()
        records = bt._pair_features(pairs)
        pairwise = bt.build_pairwise(records)
        dims = bt._identifiable_dims(pairwise)
        assert dims == [0]  # 仅 must

    def test_fit_converges_on_separable_data(self):
        """合成可分数据：BT 权重 must 方向为正且显著。"""
        pairs = _sample_pairs()
        records = bt._pair_features(pairs)
        pairwise = bt.build_pairwise(records)
        weights, ident = bt.fit_bt(pairwise)
        assert ident == [0]
        assert weights[0] > 1.0  # must 权重显著为正（可分数据收敛）

    def test_fit_all_identical_returns_zero(self):
        """全部特征相同（无信息）→ 无可识别维度，权重全 0。"""
        pairs = [( [0.5, 1.0, 1.0], [0.5, 1.0, 1.0]) for _ in range(10)]
        weights, ident = bt.fit_bt(pairs)
        assert ident == []
        assert weights == [0.0, 0.0, 0.0]


class TestDegradation:
    def test_insufficient_dims_keeps_optuna(self, tmp_path, monkeypatch, capsys):
        """数据不足（<2 维可识别）→ 打印退化提示，不写回 configs。"""
        # 用可识别维度仅 must 的合成数据跑主流程：--apply 也应不写回
        pairs = _sample_pairs()
        monkeypatch.setattr(bt, "load_pairs", lambda p: pairs)
        weights_path = tmp_path / "match_weights.json"
        monkeypatch.setattr(bt, "_WEIGHTS_PATH", weights_path)
        monkeypatch.setattr(bt, "_GOLDEN_MATCH", tmp_path / "golden.json")
        (tmp_path / "golden.json").write_text("", encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["tune_match_bt.py", "--apply"])
        bt.main()  # 数据不足分支直接 return（无 SystemExit）
        assert not weights_path.exists()  # 未写回
        assert "数据不足" in capsys.readouterr().out


class TestEvaluateWeights:
    def test_single_dim_weights_preserve_ranking(self):
        """单维特征（仅 must 有信息）下权重只做单调缩放——Spearman 排序不变。

        这是 BT/线性评分的数学性质：total = w·f，f 仅 must 变化时
        任何 w_must>0 产生相同排序。Acc（0.5 阈值）可区分。
        """
        pairs = _sample_pairs()
        bt_w = bt.evaluate_weights(pairs, (0.97, 0.03, 0.0))
        other_w = bt.evaluate_weights(pairs, (0.4, 0.3, 0.3))
        assert bt_w["spearman"] == pytest.approx(other_w["spearman"], abs=1e-9)
        assert 0.0 <= bt_w["accuracy"] <= 1.0

    def test_bt_weights_accuracy_reasonable(self):
        """BT 权重（must 主导）在可分合成数据上 Acc 应 ≥ 0.85。"""
        pairs = _sample_pairs()
        bt_w = bt.evaluate_weights(pairs, (0.97, 0.03, 0.0))
        assert bt_w["accuracy"] >= 0.85
