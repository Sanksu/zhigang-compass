"""真实 LLM 端到端评估（TE-M2-04，设计文档 §13.3）。

与 scripts/evaluate.py（基线评测）的区别：本脚本调用真实 LLM 抽取器
（JDExtractor / ResumeExtractor），评估 LLM 上线后的实际准确率。
基线评测用关键词白名单（无 LLM），本脚本用真实 LLM + 规则兜底。

输出结构兼容 scripts/evaluate.py 的 generate_html_report()，
可生成含分项得分 + 错误分析 + 混淆矩阵的 HTML 报告。

用法：
    python tests/evaluate/run_real_eval.py --task jd            # JD 真实 LLM 抽取评测
    python tests/evaluate/run_real_eval.py --task jd --limit 5  # 小批量冒烟
    python tests/evaluate/run_real_eval.py --task resume        # 简历真实 LLM 抽取评测
    python tests/evaluate/run_real_eval.py --task match         # 匹配评测（语义增强）
    python tests/evaluate/run_real_eval.py --task all           # 全部
"""

import argparse
import sys
from pathlib import Path

# 后端根目录（tests/evaluate/ → backend/）
_BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_DIR))
# 调优/评估共用的 evaluate_pairs 位于 backend/scripts/
sys.path.insert(0, str(_BACKEND_DIR / "scripts"))

from run_baseline import _norm_skill, compute_f1, keyword_match, load_golden_set  # noqa: E402

_GOLDEN_JD = _BACKEND_DIR / "data" / "golden_set" / "jd_golden_100.jsonl"
_RESUME_GOLDEN = _BACKEND_DIR / "data" / "golden_set" / "golden_set_resume.jsonl"
_MATCH_GOLDEN = _BACKEND_DIR / "data" / "golden_set" / "golden_set_match.jsonl"

# 目标阈值（设计文档 §13.3）：≥ 90%
_JD_TARGET_F1 = 0.90
_RESUME_TARGET_F1 = 0.90
_MATCH_TARGET = 0.90


def evaluate_jd_extraction(golden_set: list[dict], limit: int | None = None) -> dict:
    """JD 解析端到端评估：真实 LLM 抽取 vs gold_skills 标注（设计文档 §13.3）。

    返回结构兼容 scripts/evaluate.py 的 eval_jd()，含混淆矩阵 + 错误样例。
    """
    from app.services.extraction.jd_extractor import JDExtractor

    extractor = JDExtractor()
    total_tp, total_fp, total_fn = 0, 0, 0
    skipped, errors = 0, 0
    error_cases: list[dict] = []

    samples = golden_set[:limit] if limit else golden_set
    for i, item in enumerate(samples, 1):
        text = item.get("raw_text") or ""
        gold = item.get("gold_skills") or []
        if not text or not gold:
            skipped += 1
            continue
        try:
            result = extractor.extract(text)
            pred = [s.name for s in result.skills]
        except Exception as e:
            errors += 1
            print(f"  [{i:>3}] ERR {item.get('source_id')}: {e}")
            continue
        tp, fp, fn = keyword_match(pred, gold)
        total_tp += tp
        total_fp += fp
        total_fn += fn
        if (fp > 0 or fn > 0) and len(error_cases) < 5:
            pred_set = {_norm_skill(s) for s in pred}
            gold_set = {_norm_skill(s) for s in gold}
            error_cases.append({
                "source_id": item.get("source_id", ""),
                "false_positives": sorted(pred_set - gold_set)[:5],
                "false_negatives": sorted(gold_set - pred_set)[:5],
            })
        if i % 10 == 0 or i == len(samples):
            print(f"  ... {i}/{len(samples)} 已处理")

    precision, recall, f1 = compute_f1(total_tp, total_fp, total_fn)
    return {
        "task": "jd",
        "skipped": False,
        "method": "真实 LLM 抽取（JDExtractor + 规则兜底）",
        "samples": len(samples) - skipped - errors,
        "skipped_samples": skipped,
        "errors": errors,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "target_f1": _JD_TARGET_F1,
        "target_met": f1 >= _JD_TARGET_F1,
        "confusion": {"tp": total_tp, "fp": total_fp, "fn": total_fn},
        "error_cases": error_cases,
    }


def evaluate_resume_extraction(golden_set: list[dict]) -> dict:
    """简历提取端到端评估：真实 LLM 抽取 vs gold_skills 标注（设计文档 §13.3）。

    返回结构兼容 scripts/evaluate.py 的 eval_resume()，含混淆矩阵。
    """
    from app.services.resume.extractor import ResumeExtractor

    extractor = ResumeExtractor()
    total_tp, total_fp, total_fn = 0, 0, 0
    skipped, errors = 0, 0

    for item in golden_set:
        text = item.get("raw_text") or ""
        gold = item.get("gold_skills") or []
        if not text or not gold:
            skipped += 1
            continue
        try:
            pred = [s.name for s in extractor.extract(text).skills]
        except Exception:
            errors += 1
            continue
        # exclude_noise：与 evaluate.py 口径一致，过滤单字母噪音
        tp, fp, fn = keyword_match(pred, gold, exclude_noise=True)
        total_tp += tp
        total_fp += fp
        total_fn += fn

    if (total_tp + total_fp + total_fn) == 0:
        return {"task": "resume", "skipped": True, "reason": "黄金集无可评测样本"}

    precision, recall, f1 = compute_f1(total_tp, total_fp, total_fn)
    return {
        "task": "resume",
        "skipped": False,
        "method": "真实 LLM 抽取（ResumeExtractor + 规则兜底）",
        "samples": len(golden_set) - skipped - errors,
        "skipped_samples": skipped,
        "errors": errors,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "target_f1": _RESUME_TARGET_F1,
        "target_met": f1 >= _RESUME_TARGET_F1,
        "confusion": {"tp": total_tp, "fp": total_fp, "fn": total_fn},
    }


def evaluate_matching(golden_set: list[dict]) -> dict:
    """人岗匹配端到端评估（设计文档 §13.3）。

    语义增强匹配引擎：Spearman 秩相关 + 分类准确率 + Top-3 推荐准确率 + 混淆矩阵。
    返回结构兼容 scripts/evaluate.py 的 eval_match()。
    """
    from app.services.matching.semantic import SkillEmbedder
    from app.services.matching.weights import load_sim_threshold, load_weights

    from tune_match_weights import MATCH_CLASSIFY_THRESHOLD, evaluate_pairs

    semantic = SkillEmbedder.get()
    weights = load_weights()
    threshold = load_sim_threshold()
    result = evaluate_pairs(golden_set, weights, semantic, threshold)
    scores = result["scores"]
    labels = result["labels"]

    # 混淆矩阵（阈值 MATCH_CLASSIFY_THRESHOLD，与 evaluate_pairs 分类口径一致）
    tp = sum(1 for s, l in zip(scores, labels) if s >= MATCH_CLASSIFY_THRESHOLD and l == 1)
    fp = sum(1 for s, l in zip(scores, labels) if s >= MATCH_CLASSIFY_THRESHOLD and l == 0)
    tn = sum(1 for s, l in zip(scores, labels) if s < MATCH_CLASSIFY_THRESHOLD and l == 0)
    fn = sum(1 for s, l in zip(scores, labels) if s < MATCH_CLASSIFY_THRESHOLD and l == 1)

    # Top-3 推荐准确率（设计文档 §9.6）— 复用 evaluate.py 的实现
    from scripts.evaluate import _top3_accuracy

    top3, top3_samples = _top3_accuracy(golden_set, scores)

    # 错误样例
    error_cases: list[dict] = []
    for p, s, l in zip(golden_set, scores, labels):
        is_pred_match = s >= MATCH_CLASSIFY_THRESHOLD
        is_gold_match = l == 1
        if is_pred_match != is_gold_match and len(error_cases) < 5:
            error_cases.append({
                "position_id": p.get("position_id", ""),
                "score": round(s, 4),
                "label": l,
                "error_type": "FP" if is_pred_match and not is_gold_match else "FN",
            })

    return {
        "task": "match",
        "skipped": False,
        "method": "规则 + SBERT 语义增强",
        "spearman": round(result["spearman"], 4),
        "accuracy": round(result["accuracy"], 4),
        "target_accuracy": _MATCH_TARGET,
        "target_met": result["accuracy"] >= _MATCH_TARGET,
        "top3_accuracy": round(top3, 4) if top3 is not None else None,
        "top3_samples": top3_samples,
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "error_cases": error_cases,
    }


def main():
    parser = argparse.ArgumentParser(description="真实 LLM 端到端评估（TE-M2-04）")
    parser.add_argument("--task", choices=["jd", "resume", "match", "all"], default="all")
    parser.add_argument("--limit", type=int, default=None, help="仅评估前 N 条（冒烟测试）")
    args = parser.parse_args()

    results = []

    if args.task in ("jd", "all"):
        golden = load_golden_set(str(_GOLDEN_JD))
        if not golden:
            print("[SKIP] JD 黄金集不存在")
            results.append({"task": "jd", "skipped": True, "reason": "黄金集缺失"})
        else:
            print(f"=== JD 抽取端到端评估（{len(golden)} 条，LLM 真实调用）===")
            r = evaluate_jd_extraction(golden, args.limit)
            results.append(r)
            print(f"样本数: {r['samples']}（跳过 {r['skipped_samples']}，错误 {r['errors']}）")
            print(f"Precision: {r['precision']:.4f}")
            print(f"Recall:    {r['recall']:.4f}")
            print(f"F1:        {r['f1']:.4f}")
            print(f"目标 F1≥{r['target_f1']:.2f} -> {'达标' if r['target_met'] else '未达标'}")

    if args.task in ("resume", "all"):
        golden = load_golden_set(str(_RESUME_GOLDEN))
        if not golden:
            print("[SKIP] resume 黄金集未交付")
            results.append({"task": "resume", "skipped": True, "reason": "黄金集缺失"})
        else:
            print(f"\n=== 简历提取端到端评估（{len(golden)} 条，LLM 真实调用）===")
            r = evaluate_resume_extraction(golden)
            results.append(r)
            if r.get("skipped"):
                print(f"[SKIP] {r.get('reason', '')}")
            else:
                print(f"样本数: {r['samples']}（跳过 {r['skipped_samples']}，错误 {r['errors']}）")
                print(f"Precision: {r['precision']:.4f}")
                print(f"Recall:    {r['recall']:.4f}")
                print(f"F1:        {r['f1']:.4f}")
                print(f"目标 F1≥{r['target_f1']:.2f} -> {'达标' if r['target_met'] else '未达标'}")

    if args.task in ("match", "all"):
        golden = load_golden_set(str(_MATCH_GOLDEN))
        if not golden:
            print("[SKIP] match 黄金集不存在")
            results.append({"task": "match", "skipped": True, "reason": "黄金集缺失"})
        else:
            print(f"\n=== 人岗匹配端到端评估（{len(golden)} 对弱监督黄金集）===")
            r = evaluate_matching(golden)
            results.append(r)
            top3_str = f" Top-3={r['top3_accuracy']:.4f}" if r.get("top3_accuracy") is not None else ""
            print(f"Spearman: {r['spearman']:.4f}  Accuracy: {r['accuracy']:.4f}{top3_str}")
            print(f"目标 Acc≥{r['target_accuracy']:.2f} -> {'达标' if r['target_met'] else '未达标'}")

    print("\n" + "=" * 56)
    print("真实 LLM 端到端评估完成（TE-M2-04）")
    print("=" * 56)


if __name__ == "__main__":
    main()
