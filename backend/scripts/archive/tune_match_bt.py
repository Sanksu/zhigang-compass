"""匹配权重 Bradley-Terry 反馈学习（AL-M5-02，设计文档 9.3）。

在弱监督匹配黄金集上构造成对比较（同一候选人下，label=1 岗位 vs label=0 岗位），
用 Bradley-Terry 模型（成对逻辑回归：P(正>负) = σ(w·(f(正)-f(负)))）最大似然
估计维度权重 (w_must, w_nice, w_exp)——比 Optuna 全局搜索更贴合排序语义
（相对优劣而非绝对分数）。

**数据约束（2026-08-13 实跑结论）**：当前黄金集（300 对）position_skills 全标
MUST、无年限字段 → nice_score/exp_score 恒定 → BT 仅 must 维度可识别，其余
维度权重不可识别——按设计文档「数据不足时退化至 Optuna 静态权重」处理：
本脚本输出 BT 估计与退化判定，默认不写回 configs。

用法：
    uv run python scripts/tune_match_bt.py          # BT 拟合 + 对比评测
    uv run python scripts/tune_match_bt.py --apply  # 仅当 must 维度可识别且
                                                    # Spearman 不劣于当前才写回
"""

import argparse
import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("tune_match_bt")

_GOLDEN_MATCH = _BACKEND_DIR / "data" / "golden_set" / "golden_set_match.jsonl"
_WEIGHTS_PATH = _BACKEND_DIR / "configs" / "match_weights.json"

# BT 可识别维度下限：特征（must/nice/exp 分）在成对样本中须至少 2 维有方差，
# 否则权重不可识别（当前黄金集仅 must 有方差）
_MIN_VARIANCE_DIMS = 2


def load_pairs(path: Path) -> list[dict]:
    """加载匹配黄金集（JSONL）。"""
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _pair_features(pairs: list[dict]) -> list[tuple[str, list[float], int]]:
    """每对 (候选组 key, 三维特征, label)——按候选组分组供 BT 成对构造。

    特征 = score_position 的 radar 维度分（must/nice/exp），与权重无关。
    与 tune_match_weights.evaluate_pairs 同构造：position_skills 全标 MUST、
    total_years=5 无年限要求 → 当前黄金集 nice/exp 恒 1.0（数据约束）。
    """
    from app.services.matching.engine import score_position
    from app.services.matching.schemas import (
        CandidateProfile,
        CandidateSkill,
        Necessity,
        PositionProfile,
        SkillRequirement,
    )

    records: list[tuple[str, list[float], int]] = []
    for p in pairs:
        musts = [
            SkillRequirement(
                skill_id=s, skill_name=s, necessity=Necessity.MUST, weight=1.0
            )
            for s in p["position_skills"]
        ]
        position = PositionProfile(
            position_id=p["position_id"], name=p["position_id"], must_skills=musts
        )
        candidate = CandidateProfile(
            user_id="eval",
            skills=[
                CandidateSkill(skill_id=s, skill_name=s, proficiency=2)
                for s in p["candidate_skills"]
            ],
            total_years=5.0,
        )
        r = score_position(candidate, position, weights=(1 / 3, 1 / 3, 1 / 3))
        feats = [r.radar.get("must", 0.0), r.radar.get("nice", 0.0), r.radar.get("exp", 0.0)]
        records.append((tuple(p["candidate_skills"]), feats, p["label"]))
    return records


def build_pairwise(records: list[tuple[str, list[float], int]]) -> list[tuple[list[float], list[float]]]:
    """按候选组构造成对（同组内正样本岗位 > 负样本岗位）。

    Bradley-Terry 的成对比较语义：同一候选人在两个岗位间的相对优劣
    （跨候选人的正负对无可比性，不得交叉构造）。
    """
    from collections import defaultdict

    groups: dict[str, tuple[list[list[float]], list[list[float]]]] = defaultdict(lambda: ([], []))
    for key, feats, label in records:
        (groups[key][0] if label == 1 else groups[key][1]).append(feats)
    pairs = [
        (pos, neg)
        for pos_list, neg_list in groups.values()
        for pos in pos_list
        for neg in neg_list
    ]
    if not pairs:
        logger.warning("无同候选正负成对样本，BT 无法拟合")
    return pairs


def _identifiable_dims(pairs: list[tuple[list[float], list[float]]]) -> list[int]:
    """可识别维度：Δf 有非零方差的维度（权重可估计）。"""
    import numpy as np

    deltas = np.array([np.array(pos) - np.array(neg) for pos, neg in pairs])
    stds = deltas.std(axis=0)
    return [i for i, s in enumerate(stds) if s > 1e-9]


def fit_bt(pairs: list[tuple[list[float], list[float]]]) -> tuple[list[float], list[int]]:
    """Bradley-Terry 最大似然（成对逻辑回归）：最小化 -Σ log σ(w·Δf)。

    返回 (weights[3], identifiable_dims)。不可识别维度的权重保持 0
    （不参与排序，与"数据不足退化"语义一致）。
    """
    import numpy as np
    from scipy.optimize import minimize

    deltas = np.array([np.array(pos) - np.array(neg) for pos, neg in pairs])
    ident = _identifiable_dims(pairs)
    if not ident:
        return [0.0, 0.0, 0.0], []

    def neg_ll(w_sub: np.ndarray) -> float:
        w = np.zeros(3)
        w[ident] = w_sub
        z = deltas @ w
        # log σ(z) 数值稳定：-log(1+exp(-z))
        return float(np.sum(np.logaddexp(0.0, -z)))

    res = minimize(neg_ll, np.zeros(len(ident)), method="BFGS")
    w = [0.0, 0.0, 0.0]
    for i, dim in enumerate(ident):
        w[dim] = float(res.x[i])
    return w, ident


def evaluate_weights(pairs: list[dict], weights: tuple[float, float, float]) -> dict:
    """黄金集评测（Spearman / Accuracy），与 tune_match_weights 同口径。"""
    from scipy.stats import spearmanr

    from app.services.matching.engine import score_position
    from app.services.matching.schemas import (
        CandidateProfile,
        CandidateSkill,
        Necessity,
        PositionProfile,
        SkillRequirement,
    )

    scores, labels = [], []
    for p in pairs:
        musts = [
            SkillRequirement(skill_id=s, skill_name=s, necessity=Necessity.MUST, weight=1.0)
            for s in p["position_skills"]
        ]
        position = PositionProfile(
            position_id=p["position_id"], name=p["position_id"], must_skills=musts
        )
        candidate = CandidateProfile(
            user_id="eval",
            skills=[
                CandidateSkill(skill_id=s, skill_name=s, proficiency=2)
                for s in p["candidate_skills"]
            ],
            total_years=5.0,
        )
        r = score_position(candidate, position, weights=weights)
        scores.append(r.total_score)
        labels.append(p["label"])
    hits = sum(1 for s, lb in zip(scores, labels) if (s >= 0.5) == (lb == 1))
    corr = spearmanr(scores, labels).statistic
    return {
        "spearman": float(corr) if corr == corr else 0.0,
        "accuracy": hits / len(scores) if scores else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="匹配权重 Bradley-Terry 反馈学习（AL-M5-02）")
    parser.add_argument("--apply", action="store_true", help="BT 更优且维度可识别时写回 configs/match_weights.json")
    args = parser.parse_args()

    if not _GOLDEN_MATCH.exists():
        logger.warning(f"匹配黄金集不存在: {_GOLDEN_MATCH}")
        return
    pairs = load_pairs(_GOLDEN_MATCH)
    records = _pair_features(pairs)
    pairwise = build_pairwise(records)
    logger.info("成对样本: %s（同候选组内正负对）", len(pairwise))

    weights, ident = fit_bt(pairwise)
    dim_names = ["must", "nice", "exp"]
    print(f"[BT 拟合] 可识别维度: {[dim_names[i] for i in ident] or '无'}")
    print(f"  w_must={weights[0]:.4f} w_nice={weights[1]:.4f} w_exp={weights[2]:.4f}")

    from app.services.matching.weights import load_weights

    current = load_weights()
    if len(ident) < _MIN_VARIANCE_DIMS:
        print(
            f"  ⚠️ 数据不足：仅 {len(ident)} 维可识别（当前黄金集全 must 标注、无年限）"
            f"——按设计文档『数据不足退化至 Optuna 静态权重』，保持 configs 权重 "
            f"({current[0]:.3f}/{current[1]:.3f}/{current[2]:.3f}) 不写回"
        )
        return

    # BT 归一化权重（与 Optuna 权重同量纲：和=1）
    w_sum = sum(weights)
    bt_norm = tuple(w / w_sum for w in weights) if w_sum > 0 else current
    m_bt = evaluate_weights(pairs, bt_norm)
    m_cur = evaluate_weights(pairs, current)
    print(f"[对比] BT 权重 {tuple(round(x, 3) for x in bt_norm)}: Spearman={m_bt['spearman']:.4f} Acc={m_bt['accuracy']:.4f}")
    print(f"[对比] 当前 Optuna 权重 {tuple(round(x, 3) for x in current)}: Spearman={m_cur['spearman']:.4f} Acc={m_cur['accuracy']:.4f}")

    if args.apply and m_bt["spearman"] >= m_cur["spearman"] - 1e-9:
        _WEIGHTS_PATH.write_text(
            json.dumps(
                {
                    "w_must": round(bt_norm[0], 4),
                    "w_nice": round(bt_norm[1], 4),
                    "w_exp": round(bt_norm[2], 4),
                    "sim_threshold": 0.831,
                    "_comment": "Bradley-Terry 反馈学习估计（AL-M5-02），Spearman 对比不劣于 Optuna",
                    "_metrics": {"spearman": m_bt["spearman"], "accuracy": m_bt["accuracy"]},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("BT 权重已写入 %s", _WEIGHTS_PATH)
    elif args.apply:
        print("BT 权重未优于当前，不写回")


if __name__ == "__main__":
    main()
