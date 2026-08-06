"""真实 LLM 端到端评估。

M3 实装 JD 抽取评测：调用 app.services.extraction.jd_extractor
（instructor 强校验 + 规则兜底）对黄金集 raw_text 抽取技能，与 gold_skills
标注比对 F1。目标：F1 ≥ 0.90（设计文档 1.1 简历解析准确率 ≥ 90%）。

match 评测（AL-M3-03）：弱监督匹配黄金集（golden_set_match.jsonl）上
Spearman 秩相关 ≥ 0.85 + 二分类准确率。resume 评测待 M4 简历黄金集交付。

用法：
    python tests/evaluate/run_real_eval.py --task jd            # 全量 100 条
    python tests/evaluate/run_real_eval.py --task jd --limit 5  # 小批量冒烟
    python tests/evaluate/run_real_eval.py --task match         # 匹配评估（需已生成弱监督黄金集）
"""

import argparse
import sys
from pathlib import Path

# 后端根目录（tests/evaluate/ → backend/）
_BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_DIR))
# 调优/评估共用的 evaluate_pairs 位于 backend/scripts/
sys.path.insert(0, str(_BACKEND_DIR / "scripts"))

from run_baseline import compute_f1, keyword_match, load_golden_set

_GOLDEN_JD = _BACKEND_DIR / "data" / "golden_set" / "jd_golden_100.jsonl"


def evaluate_jd_extraction(golden_set: list[dict], limit: int | None = None) -> dict:
    """JD 解析端到端评估：真实 LLM 抽取 vs gold_skills 标注。"""
    from app.services.extraction.jd_extractor import JDExtractor

    extractor = JDExtractor()
    total_tp, total_fp, total_fn = 0, 0, 0
    skipped, errors = 0, 0

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
        if i % 10 == 0 or i == len(samples):
            print(f"  ... {i}/{len(samples)} 已处理")

    precision, recall, f1 = compute_f1(total_tp, total_fp, total_fn)
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "samples": len(samples) - skipped - errors,
        "skipped": skipped,
        "errors": errors,
    }


def evaluate_resume_extraction(golden_set: list[dict]) -> dict:
    """简历提取端到端评估。"""
    # TODO: M4 阶段实现（依赖简历解析全闭环 + 简历黄金集）
    return {"f1": None}


def evaluate_matching(golden_set: list[dict]) -> dict:
    """人岗匹配端到端评估（AL-M3-03）。

    在弱监督匹配黄金集（data/golden_set/golden_set_match.jsonl，build_match_golden.py
    生成）上评估语义增强匹配引擎：Spearman 秩相关 + 二分类准确率。
    """
    from app.services.matching.semantic import SkillEmbedder
    from app.services.matching.weights import load_sim_threshold, load_weights

    # 评估逻辑与 Optuna 调优共用（scripts/tune_match_weights.py）
    from tune_match_weights import evaluate_pairs

    semantic = SkillEmbedder.get()
    result = evaluate_pairs(golden_set, load_weights(), semantic, load_sim_threshold())
    return {"spearman": result["spearman"], "accuracy": result["accuracy"]}


def main():
    parser = argparse.ArgumentParser(description="真实 LLM 端到端评估")
    parser.add_argument("--task", choices=["jd", "resume", "match", "all"], default="all")
    parser.add_argument("--limit", type=int, default=None, help="仅评估前 N 条（冒烟测试）")
    args = parser.parse_args()

    if args.task in ("jd", "all"):
        golden = load_golden_set(str(_GOLDEN_JD))
        if not golden:
            print("[SKIP] JD 黄金集不存在")
        else:
            print(f"=== JD 抽取端到端评估（{len(golden)} 条，LLM 真实调用）===")
            results = evaluate_jd_extraction(golden, args.limit)
            print(f"样本数: {results['samples']}（跳过 {results['skipped']}，错误 {results['errors']}）")
            print(f"Precision: {results['precision']:.4f}")
            print(f"Recall:    {results['recall']:.4f}")
            print(f"F1:        {results['f1']:.4f}")
            if results["f1"] is not None:
                mark = "[OK] 达到 M3 目标" if results["f1"] >= 0.90 else "[WARN] 未达 0.90，需排查误抽/漏抽"
                print(mark)

    if args.task in ("resume", "all"):
        golden = load_golden_set(str(_BACKEND_DIR / "data" / "golden_set" / "golden_set_resume.jsonl"))
        if golden:
            results = evaluate_resume_extraction(golden)
            print(f"[Resume] F1: {results.get('f1', 'N/A')}")
        else:
            print("[SKIP] resume 黄金集未交付（M4）")

    if args.task in ("match", "all"):
        golden = load_golden_set(str(_BACKEND_DIR / "data" / "golden_set" / "golden_set_match.jsonl"))
        if golden:
            print(f"=== 人岗匹配端到端评估（{len(golden)} 对弱监督黄金集）===")
            results = evaluate_matching(golden)
            print(f"Spearman: {results.get('spearman', 'N/A'):.4f}")
            print(f"Accuracy: {results.get('accuracy', 'N/A'):.4f}")
            spearman = results.get("spearman")
            if spearman is not None:
                mark = "[OK] 达到 M3 目标" if spearman >= 0.85 else "[WARN] 未达 0.85"
                print(mark)
        else:
            print("[SKIP] match 黄金集不存在（先运行 scripts/build_match_golden.py）")


if __name__ == "__main__":
    main()
