"""时滞检测阈值调优（DA-M3-04，设计文档 §4.7）。

在时滞序列标注集（build_temporal_golden.py 生成）上评估三类检测
（SAI 内容时滞 / 僵尸 JD / 抄袭时滞）的召回率与误报率，控制目标
与设计文档 §4.8 对齐：召回率 ≥ 80%、误报率 ≤ 15%。

设计文档 §4.7.2/4.7.3 已给出默认阈值（SAI 1.5/2.0、Jaccard 0.95、
连续 4 周期、间隔 90 天），调优仅在默认阈值不达标时搜索微调。

用法：
    uv run python scripts/tune_temporal.py --eval-only    # 评估设计文档默认阈值
    uv run python scripts/tune_temporal.py --trials 50    # 不达标时 Optuna 搜索
"""

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_GOLDEN = _BACKEND_DIR / "data" / "golden_set" / "golden_set_temporal.jsonl"

RECALL_TARGET = 0.80
FPR_MAX = 0.15

# 设计文档 §4.7 默认阈值
DEFAULT_THRESHOLDS = {
    "sai_stale": 1.5,
    "sai_obsolete": 2.0,
    "zombie_jaccard": 0.95,
    "zombie_min_periods": 4,
    "zombie_sai": 1.5,
    "plagiarism_days": 90,
}


def load_golden(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _predict(record: dict, thresholds: dict) -> bool:
    """按标注集输入调用检测器，返回是否判为时滞异常。"""
    from app.services.data_quality.schemas import JDSkillSet
    from app.services.data_quality.temporal_detector import (
        classify_sai,
        compute_sai,
        detect_plagiarism,
        detect_zombie_jd,
    )

    kind = record["kind"]
    if kind == "sai":
        sai = compute_sai(record["jd_skill_ages"], record["position_recent_skill_ages"])
        return classify_sai(
            sai,
            stale_threshold=thresholds["sai_stale"],
            obsolete_threshold=thresholds["sai_obsolete"],
        ).label != "fresh"
    if kind == "zombie":
        history = [set(s) for s in record["history_skills"]]
        current = set(record["current_skills"])
        return detect_zombie_jd(
            history,
            current,
            record["sai"],
            jaccard_threshold=thresholds["zombie_jaccard"],
            min_periods=thresholds["zombie_min_periods"],
            sai_threshold=thresholds["zombie_sai"],
        ).is_zombie
    # plagiarism
    today = date.today()
    old = JDSkillSet(
        jd_id="old", position_name="eval", publish_date=today - timedelta(days=record["old_days_ago"]),
        skills=record["old_skills"],
    )
    new = JDSkillSet(
        jd_id="new", position_name="eval", publish_date=today, skills=record["new_skills"],
    )
    return detect_plagiarism(new, old, days_threshold=thresholds["plagiarism_days"]).is_plagiarism


def evaluate(records: list[dict], thresholds: dict) -> dict:
    """全局召回率 / 误报率（三类异常合并统计）。"""
    tp = fp = fn = tn = 0
    for r in records:
        pred = _predict(r, thresholds)
        gold = r["gold_abnormal"]
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
    return {"recall": recall, "false_positive_rate": fpr, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def _objective(records: list[dict], trial) -> float:
    """Optuna 目标：召回率最大化，误报率超限重罚。

    搜索空间以设计文档默认阈值为中心的小邻域（阈值仅微调，不偏离文档语义）。
    """
    thresholds = {
        "sai_stale": trial.suggest_float("sai_stale", 1.3, 1.7),
        "sai_obsolete": trial.suggest_float("sai_obsolete", 1.8, 2.2),
        "zombie_jaccard": trial.suggest_float("zombie_jaccard", 0.90, 0.98),
        "zombie_min_periods": trial.suggest_int("zombie_min_periods", 3, 5),
        "zombie_sai": trial.suggest_float("zombie_sai", 1.3, 1.7),
        "plagiarism_days": trial.suggest_int("plagiarism_days", 60, 120),
    }
    result = evaluate(records, thresholds)
    return result["recall"] - 0.5 * max(0.0, result["false_positive_rate"] - FPR_MAX)


def tune(records: list[dict], n_trials: int) -> dict:
    import optuna

    study = optuna.create_study(direction="maximize")
    study.optimize(lambda t: _objective(records, t), n_trials=n_trials, show_progress_bar=True)
    thresholds = {k: v for k, v in study.best_params.items()}
    # 保证 stale < obsolete（约束）
    thresholds["sai_stale"] = min(thresholds["sai_stale"], thresholds["sai_obsolete"] - 0.1)
    return {"thresholds": thresholds, "metrics": evaluate(records, thresholds)}


def report(title: str, metrics: dict, thresholds: dict | None = None) -> None:
    print(f"[{title}]")
    if thresholds:
        print(f"  阈值: {json.dumps(thresholds, ensure_ascii=False)}")
    print(f"  召回率 = {metrics['recall']:.4f}（目标 ≥ {RECALL_TARGET:.2f}）"
          f"  误报率 = {metrics['false_positive_rate']:.4f}（目标 ≤ {FPR_MAX:.2f}）")
    print(f"  TP={metrics['tp']} FP={metrics['fp']} FN={metrics['fn']} TN={metrics['tn']}")
    ok = metrics["recall"] >= RECALL_TARGET and metrics["false_positive_rate"] <= FPR_MAX
    print(f"  {'[OK] 达到调优目标' if ok else '[WARN] 未达标'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="时滞检测阈值调优")
    parser.add_argument("--trials", type=int, default=50)
    parser.add_argument("--eval-only", action="store_true", help="仅评估设计文档默认阈值")
    args = parser.parse_args()

    if not _GOLDEN.exists():
        print(f"[SKIP] 时滞标注集不存在: {_GOLDEN}（先运行 scripts/build_temporal_golden.py）")
        return
    records = load_golden(_GOLDEN)
    kinds = {}
    for r in records:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(f"标注集: {len(records)} 条 {kinds}")

    if args.eval_only:
        metrics = evaluate(records, DEFAULT_THRESHOLDS)
        report("评估设计文档默认阈值", metrics, DEFAULT_THRESHOLDS)
        if metrics["recall"] >= RECALL_TARGET and metrics["false_positive_rate"] <= FPR_MAX:
            print("默认阈值达标，无需调优（设计文档 §4.7 阈值直接采用）")
        return

    result = tune(records, n_trials=args.trials)
    report("调优完成", result["metrics"], result["thresholds"])


if __name__ == "__main__":
    main()
