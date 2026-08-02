"""通胀检测权重 Optuna 调优（DA-M3-04，设计文档 §4.8）。

在合成通胀标注集（build_inflation_golden.py 生成）上搜索四维权重
(experience, skill_count, skill_depth, education)，目标最大化召回率，
约束误报率 ≤ 15%（设计文档控制目标）。

用法：
    uv run python scripts/tune_inflation.py                 # Optuna 调优（默认 50 次）
    uv run python scripts/tune_inflation.py --trials 100
    uv run python scripts/tune_inflation.py --eval-only     # 仅评估当前 configs/inflation_weights.json

最优权重写入 `configs/inflation_weights.json`（幂等覆盖，可审计）。
"""

import argparse
import json
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_GOLDEN = _BACKEND_DIR / "data" / "golden_set" / "golden_set_inflation.jsonl"
_WEIGHTS_PATH = _BACKEND_DIR / "configs" / "inflation_weights.json"

# 设计文档 §4.8 控制目标
RECALL_TARGET = 0.80
FPR_MAX = 0.15


def load_golden(path: Path) -> list[dict]:
    """加载通胀标注集（JSONL）。"""
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def evaluate(records: list[dict], weights: dict[str, float]) -> dict:
    """按给定权重评估标注集，返回 recall / false_positive_rate / 各级别混淆数。

    预测口径：inflation_score 分级，label != normal 视为检出通胀。
    """
    from app.services.data_quality.inflation_detector import compute_inflation_score

    tp = fp = fn = tn = 0
    for r in records:
        pred = (
            compute_inflation_score(
                r["job_level"],
                r["min_years"],
                r["skill_count"],
                r["expert_level_count"],
                r["education"],
                weights=weights,
            ).label
            != "normal"
        )
        gold = r["is_inflation"]
        if gold and pred:
            tp += 1
        elif not gold and pred:
            fp += 1
        elif gold and not pred:
            fn += 1
        else:
            tn += 1

    recall = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    return {
        "recall": recall,
        "false_positive_rate": fpr,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    }


def _objective(records: list[dict], trial) -> float:
    """Optuna 目标：召回率最大化，误报率超限重罚。

    四维权重独立搜索 [0.1, 0.75]（不强制和为 1）：单维溢出分满分为 1.0，
    需要对应权重 ≥ 0.4 才能单独触发 0.4 通胀阈值；轻度溢出（分 0.4-0.6）
    需要权重 ≥ 0.67 才能检出，故上限放宽到 0.75。合理样本全维低分，
    权重放大不会误报（目标函数对误报重罚兜底）。
    """
    weights = {
        "experience": trial.suggest_float("experience", 0.1, 0.75),
        "skill_count": trial.suggest_float("skill_count", 0.1, 0.75),
        "skill_depth": trial.suggest_float("skill_depth", 0.1, 0.75),
        "education": trial.suggest_float("education", 0.1, 0.75),
    }
    result = evaluate(records, weights)
    # 召回率为主目标，误报率 > 15% 时每超 1% 重罚 0.5
    return result["recall"] - 0.5 * max(0.0, result["false_positive_rate"] - FPR_MAX)


def tune(records: list[dict], n_trials: int) -> dict:
    """Optuna 搜索四维权重，返回最优参数 + 达标评估。"""
    import optuna

    study = optuna.create_study(direction="maximize")
    study.optimize(lambda t: _objective(records, t), n_trials=n_trials, show_progress_bar=True)

    best = study.best_params
    weights = {k: round(v, 3) for k, v in best.items()}
    return {"weights": weights, "metrics": evaluate(records, weights)}


def write_weights(weights: dict, metrics: dict) -> None:
    """最优权重写入 configs/inflation_weights.json（保留说明字段）。"""
    existing = {}
    if _WEIGHTS_PATH.exists():
        try:
            existing = json.loads(_WEIGHTS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    out = {
        **weights,
        "_comment": existing.get(
            "_comment",
            "通胀检测四维权重（设计文档 §4.8）。Optuna 搜索覆盖此文件。",
        ),
        "_metrics": {
            "recall": round(metrics["recall"], 4),
            "false_positive_rate": round(metrics["false_positive_rate"], 4),
        },
    }
    _WEIGHTS_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def report(title: str, metrics: dict, weights: dict | None = None) -> None:
    print(f"[{title}]")
    if weights:
        print(f"  权重: {json.dumps(weights, ensure_ascii=False)}")
    print(f"  召回率 = {metrics['recall']:.4f}（目标 ≥ {RECALL_TARGET:.2f}）"
          f"  误报率 = {metrics['false_positive_rate']:.4f}（目标 ≤ {FPR_MAX:.2f}）")
    print(f"  TP={metrics['tp']} FP={metrics['fp']} FN={metrics['fn']} TN={metrics['tn']}")
    ok = metrics["recall"] >= RECALL_TARGET and metrics["false_positive_rate"] <= FPR_MAX
    print(f"  {'[OK] 达到调优目标' if ok else '[WARN] 未达标，需调整搜索空间或标注集'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="通胀检测权重 Optuna 调优")
    parser.add_argument("--trials", type=int, default=50)
    parser.add_argument("--eval-only", action="store_true", help="仅评估当前配置文件，不调优")
    args = parser.parse_args()

    if not _GOLDEN.exists():
        print(f"[SKIP] 通胀标注集不存在: {_GOLDEN}（先运行 scripts/build_inflation_golden.py）")
        return
    records = load_golden(_GOLDEN)
    print(f"标注集: {len(records)} 条（正 {sum(1 for r in records if r['is_inflation'])} / 负 {sum(1 for r in records if not r['is_inflation'])}）")

    if args.eval_only:
        from app.services.data_quality.inflation_detector import load_weights
        weights = load_weights()
        report("评估当前配置", evaluate(records, weights), weights)
        return

    result = tune(records, n_trials=args.trials)
    write_weights(result["weights"], result["metrics"])
    report(f"调优完成（写入 {_WEIGHTS_PATH.relative_to(_BACKEND_DIR)}）", result["metrics"], result["weights"])


if __name__ == "__main__":
    main()
