"""匹配分类判定线阈值测试（2026-08-30 阈值 PR）。

审计+仿真确认：0.5→0.57 令 v2 黄金集 384 对 Acc 0.8906→0.9141 达标 ≥0.90；
阈值常量应收敛在 tune_match_weights.py 单一来源，评测端(evaluate.py)与之同源。
"""

import scripts.tune_match_weights as twn


def test_classify_threshold_value_and_scope():
    """判定线常量取审计定参值。"""
    assert twn.MATCH_CLASSIFY_THRESHOLD == 0.57
    assert 0.5 < twn.MATCH_CLASSIFY_THRESHOLD <= 0.6


def test_classify_threshold_eval_sync():
    """evaluate.py（生效报告）与 tune_match_weights（得分计算）用同一判定线。"""
    import scripts.evaluate as ev

    # evaluate.py 在模块顶层 import 该常量 → 作为属性可读
    assert getattr(ev, "MATCH_CLASSIFY_THRESHOLD", None) == 0.57


def test_v2_full_accuracy_meets_target():
    """v2 全量 384 对在**真实权重+新判定线**下 Acc ≥0.90（验收回归，不触库）。"""
    from tests.evaluate.test_match_real_jd import _match_v2_rows
    from app.services.matching.weights import load_sim_threshold, load_weights

    v2 = _match_v2_rows()
    result = twn.evaluate_pairs(v2, load_weights(), None, load_sim_threshold())
    assert result["accuracy"] >= 0.90, f"真实权重+新判定线下 v2 Acc={result['accuracy']:.4f} 未达标"