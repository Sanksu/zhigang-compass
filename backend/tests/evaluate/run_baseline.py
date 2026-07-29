"""关键词基线评估（无 LLM）。

验证黄金集质量下限：纯关键词匹配应达到 F1 ≈ 0.75。
若 baseline 低于 0.60，说明黄金集标注质量有问题。
"""

import json
from pathlib import Path
from collections import Counter


def load_golden_set(path: str) -> list[dict]:
    """加载 JSONL 格式黄金集。"""
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def keyword_match(pred_skills: list[str], gold_skills: list[str]) -> tuple[int, int, int]:
    """关键词精确匹配，返回 (true_positive, false_positive, false_negative)。"""
    pred_set = {s.lower().strip() for s in pred_skills}
    gold_set = {s.lower().strip() for s in gold_skills}
    tp = len(pred_set & gold_set)
    fp = len(pred_set - gold_set)
    fn = len(gold_set - pred_set)
    return tp, fp, fn


def compute_f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """计算 precision / recall / F1。"""
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def evaluate_jd(golden_set: list[dict]) -> dict:
    """JD 解析基线评估。"""
    total_tp, total_fp, total_fn = 0, 0, 0
    for item in golden_set:
        pred = item.get("pred_skills", [])
        gold = item.get("gold_skills", [])
        tp, fp, fn = keyword_match(pred, gold)
        total_tp += tp
        total_fp += fp
        total_fn += fn
    precision, recall, f1 = compute_f1(total_tp, total_fp, total_fn)
    return {"precision": precision, "recall": recall, "f1": f1, "samples": len(golden_set)}


def main():
    golden_path = Path(__file__).parent / "golden_set_jd.jsonl"
    if not golden_path.exists():
        print(f"[SKIP] 黄金集不存在: {golden_path}")
        return

    golden_set = load_golden_set(str(golden_path))
    results = evaluate_jd(golden_set)

    print("=" * 50)
    print("JD 解析基线评估（关键词匹配，无 LLM）")
    print("=" * 50)
    print(f"样本数: {results['samples']}")
    print(f"Precision: {results['precision']:.4f}")
    print(f"Recall:    {results['recall']:.4f}")
    print(f"F1:        {results['f1']:.4f}")
    print()

    if results["f1"] < 0.60:
        print("[WARN] 基线 F1 < 0.60，黄金集标注质量可能有问题")
    elif results["f1"] > 0.90:
        print("[INFO] 基线 F1 > 0.90，黄金集可能过于简单")
    else:
        print("[OK] 基线 F1 在合理区间 [0.60, 0.90]")


if __name__ == "__main__":
    main()
