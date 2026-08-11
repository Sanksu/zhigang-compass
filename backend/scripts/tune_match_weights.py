"""匹配权重/阈值 Optuna 调优（AL-M3-03，设计文档 9.3）。

在弱监督匹配黄金集（build_match_golden.py 生成）上搜索
(w_must, w_nice, w_exp, sim_threshold)，目标最大化 Spearman 秩相关。

用法：
    uv run python scripts/tune_match_weights.py            # 语义增强调优（SBERT）
    uv run python scripts/tune_match_weights.py --no-semantic   # 纯规则基线调优
    uv run python scripts/tune_match_weights.py --eval-only     # 仅评估当前 configs/match_weights.json
    uv run python scripts/tune_match_weights.py --trials 20     # 控制搜索次数

最优参数写入 `configs/match_weights.json`（幂等覆盖，可审计）。
"""

import argparse
import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("tune_match_weights")

_GOLDEN_MATCH = _BACKEND_DIR / "data" / "golden_set" / "golden_set_match.jsonl"
_WEIGHTS_PATH = _BACKEND_DIR / "configs" / "match_weights.json"


def load_pairs(path: Path) -> list[dict]:
    """加载匹配黄金集（JSONL）。"""
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def evaluate_pairs(pairs: list[dict], weights, semantic, sim_threshold: float) -> dict:
    """对每对计算 total_score，与 label 计算 Spearman 与分类准确率。

    返回 dict；Spearman 全相同分时为 nan，转为 0.0（避免 Optuna 崩溃）。
    """
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
        r = score_position(
            candidate, position, weights=weights, semantic=semantic, sim_threshold=sim_threshold
        )
        scores.append(r.total_score)
        labels.append(p["label"])

    # 分类准确率：total_score ≥ 0.5 视为"匹配"
    hits = sum(1 for s, lb in zip(scores, labels) if (s >= 0.5) == (lb == 1))
    accuracy = hits / len(scores) if scores else 0.0
    corr = spearmanr(scores, labels).statistic
    return {
        "spearman": float(corr) if corr == corr else 0.0,
        "accuracy": accuracy,
        "scores": scores,
        "labels": labels,
    }


def _weights_tuple(w_must: float, w_nice: float, w_exp: float) -> tuple[float, float, float]:
    return (w_must, w_nice, w_exp)


def tune(pairs: list[dict], semantic, n_trials: int) -> dict:
    """Optuna 搜索权重 + sim_threshold，返回最优参数 dict。

    semantic 为 None 时退化为纯规则调优（sim_threshold 仍参与搜索但无效）。
    """
    import optuna

    def objective(trial) -> float:
        # 搜索空间保证 w_exp = 1 - w_must - w_nice ≥ 0（total_score 不允许为负）
        w_must = trial.suggest_float("w_must", 0.4, 0.7)
        w_nice = trial.suggest_float("w_nice", 0.1, 0.3)
        w_exp = 1.0 - w_must - w_nice
        threshold = trial.suggest_float("sim_threshold", 0.5, 0.95)
        return evaluate_pairs(pairs, _weights_tuple(w_must, w_nice, w_exp), semantic, threshold)["spearman"]

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    best = study.best_params
    return {
        "w_must": round(best["w_must"], 3),
        "w_nice": round(best["w_nice"], 3),
        "w_exp": round(1.0 - best["w_must"] - best["w_nice"], 3),
        "sim_threshold": round(best["sim_threshold"], 3),
        "_spearman": study.best_value,
    }


def write_weights(params: dict) -> None:
    """最优参数写入 configs/match_weights.json（保留说明字段）。"""
    existing = {}
    if _WEIGHTS_PATH.exists():
        try:
            existing = json.loads(_WEIGHTS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    out = {
        "w_must": params["w_must"],
        "w_nice": params["w_nice"],
        "w_exp": params["w_exp"],
        "sim_threshold": params["sim_threshold"],
        "_comment": existing.get("_comment", "匹配权重（设计文档 9.3）。Optuna 搜索覆盖此文件。"),
    }
    _WEIGHTS_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="匹配权重 Optuna 调优")
    parser.add_argument("--trials", type=int, default=50)
    parser.add_argument("--no-semantic", action="store_true", help="纯规则调优（不注入 SBERT）")
    parser.add_argument("--eval-only", action="store_true", help="仅评估当前配置文件，不调优")
    args = parser.parse_args()

    if not _GOLDEN_MATCH.exists():
        logger.warning("匹配黄金集不存在: %s（先运行 scripts/build_match_golden.py）", _GOLDEN_MATCH)
        return

    pairs = load_pairs(_GOLDEN_MATCH)
    semantic = None
    if not args.no_semantic and not args.eval_only:
        from app.services.matching.semantic import SkillEmbedder
        semantic = SkillEmbedder.get()
        logger.info("语义增强已启用（SBERT），首次加载模型需数秒...")

    if args.eval_only:
        from app.services.matching.weights import load_sim_threshold, load_weights

        weights = load_weights()
        threshold = load_sim_threshold()
        if not args.no_semantic:
            from app.services.matching.semantic import SkillEmbedder
            semantic = SkillEmbedder.get()
        result = evaluate_pairs(pairs, weights, semantic, threshold)
        logger.info("[评估] 当前权重 %s sim_threshold=%s", weights, threshold)
        logger.info("       Spearman=%.4f  Accuracy=%.4f", result["spearman"], result["accuracy"])
        return

    best = tune(pairs, semantic, n_trials=args.trials)
    spearman = best.pop("_spearman")
    write_weights(best)
    logger.info("[调优完成] Spearman=%.4f", spearman)
    logger.info("          权重写入 %s", _WEIGHTS_PATH.relative_to(_BACKEND_DIR))


if __name__ == "__main__":
    main()
