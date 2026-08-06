"""准确率评测统一入口（AL-M4-04，设计文档 §13.3）。

统一评测命令：`python scripts/evaluate.py --task all`，输出 reports/eval_{date}.json。

当前覆盖（离线可复现，无需 LLM API）：
- jd    JD 解析：白名单关键词基线，字段级 F1（黄金集 data/golden_set/jd_golden_100.jsonl）
- match 人岗匹配：total_score 与人工标注的 Spearman 秩相关 + 分类准确率
        （黄金集 data/golden_set/golden_set_match.jsonl，权重来自 configs/match_weights.json）
- resume 简历提取：真实抽取（LLM + 规则兜底）vs 简历黄金集 F1
        （黄金集 data/golden_set/golden_set_resume.jsonl；未交付时跳过并注明）

LLM 抽取/简历提取的在线评测需配置 provider（configs/llm_providers.yaml）与对应黄金集，
本脚本对缺失项跳过并注明，不伪造结果。

用法：
    uv run python scripts/evaluate.py --task all        # 全部（缺黄金集项自动跳过）
    uv run python scripts/evaluate.py --task jd
    uv run python scripts/evaluate.py --task resume
    uv run python scripts/evaluate.py --task match --semantic   # 匹配项注入 SBERT 语义增强
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from scripts.tune_match_weights import evaluate_pairs, load_pairs  # noqa: E402
from tests.evaluate.run_baseline import evaluate_jd, keyword_match, load_golden_set  # noqa: E402

# 目标阈值（设计文档 §13.3 / §9.6）：≥ 90%
_JD_TARGET_F1 = 0.90
_RESUME_TARGET_F1 = 0.90
_MATCH_TARGET = 0.90

_JD_GOLDEN = _BACKEND_DIR / "data" / "golden_set" / "jd_golden_100.jsonl"
_RESUME_GOLDEN = _BACKEND_DIR / "data" / "golden_set" / "golden_set_resume.jsonl"
_MATCH_GOLDEN = _BACKEND_DIR / "data" / "golden_set" / "golden_set_match.jsonl"
_REPORT_DIR = _BACKEND_DIR / "reports"


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M")


def eval_jd() -> dict:
    """JD 解析评测：白名单关键词基线（离线确定性）。"""
    if not _JD_GOLDEN.exists():
        return {"task": "jd", "skipped": True, "reason": f"黄金集缺失: {_JD_GOLDEN.relative_to(_BACKEND_DIR)}"}
    result = evaluate_jd(load_golden_set(str(_JD_GOLDEN)))
    return {
        "task": "jd",
        "skipped": False,
        "method": "关键词基线（无 LLM，离线）",
        "samples": result["samples"],
        "precision": round(result["precision"], 4),
        "recall": round(result["recall"], 4),
        "f1": round(result["f1"], 4),
        "target_f1": _JD_TARGET_F1,
        "target_met": result["f1"] >= _JD_TARGET_F1,
    }


def eval_resume() -> dict:
    """简历提取评测：真实抽取（LLM + 规则兜底）vs 简历黄金集字段级 F1。

    黄金集每行为 {raw_text, gold_skills, ...}（与 JD 黄金集同构）。
    未交付时跳过（M5 补齐，见设计文档 §13.3 简历提取 ≥ 90%）。
    """
    if not _RESUME_GOLDEN.exists():
        return {"task": "resume", "skipped": True, "reason": f"黄金集缺失: {_RESUME_GOLDEN.relative_to(_BACKEND_DIR)}"}
    from app.services.resume.extractor import ResumeExtractor

    extractor = ResumeExtractor()
    total_tp, total_fp, total_fn, skipped, errors = 0, 0, 0, 0, 0
    for item in load_golden_set(str(_RESUME_GOLDEN)):
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
        tp, fp, fn = keyword_match(pred, gold)
        total_tp += tp
        total_fp += fp
        total_fn += fn

    if (total_tp + total_fp + total_fn) == 0:
        return {"task": "resume", "skipped": True, "reason": "黄金集无可评测样本"}
    precision, recall, f1 = _f1(total_tp, total_fp, total_fn)
    return {
        "task": "resume",
        "skipped": False,
        "method": "真实抽取（LLM + 规则兜底）",
        "samples": len(load_golden_set(str(_RESUME_GOLDEN))) - skipped - errors,
        "skipped_samples": skipped,
        "errors": errors,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "target_f1": _RESUME_TARGET_F1,
        "target_met": f1 >= _RESUME_TARGET_F1,
    }


def _f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """precision / recall / F1（与 tests/evaluate/run_baseline 同口径）。"""
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def eval_match(semantic: bool) -> dict:
    """人岗匹配评测：Spearman 秩相关 + 分类准确率。"""
    if not _MATCH_GOLDEN.exists():
        return {"task": "match", "skipped": True, "reason": f"黄金集缺失: {_MATCH_GOLDEN.relative_to(_BACKEND_DIR)}"}
    from app.services.matching.weights import load_sim_threshold, load_weights

    weights = load_weights()
    threshold = load_sim_threshold()
    sem = None
    method = "规则匹配（无语义）"
    if semantic:
        from app.services.matching.semantic import SkillEmbedder

        sem = SkillEmbedder.get()
        method = "规则 + SBERT 语义增强"
    result = evaluate_pairs(load_pairs(_MATCH_GOLDEN), weights, sem, threshold)
    return {
        "task": "match",
        "skipped": False,
        "method": method,
        "spearman": round(result["spearman"], 4),
        "accuracy": round(result["accuracy"], 4),
        "target_accuracy": _MATCH_TARGET,
        "target_met": result["accuracy"] >= _MATCH_TARGET,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="准确率评测统一入口（设计文档 §13.3）")
    parser.add_argument("--task", choices=["jd", "resume", "match", "all"], default="all")
    parser.add_argument("--semantic", action="store_true", help="匹配评测注入 SBERT 语义增强")
    args = parser.parse_args()

    results = []
    if args.task in ("jd", "all"):
        results.append(eval_jd())
    if args.task in ("resume", "all"):
        results.append(eval_resume())
    if args.task in ("match", "all"):
        results.append(eval_match(args.semantic))

    report = {
        "generated_at": _now(),
        "target": "三项准确率 ≥ 90%（设计文档 §13.3）",
        "results": results,
    }

    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _REPORT_DIR / f"eval_{datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d')}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 56)
    print("准确率评测报告（AL-M4-04）")
    print("=" * 56)
    for r in results:
        if r.get("skipped"):
            print(f"[SKIP] {r['task']}: {r['reason']}")
            continue
        if r["task"] == "jd":
            print(f"JD 解析   P={r['precision']:.4f} R={r['recall']:.4f} F1={r['f1']:.4f} "
                  f"({r['samples']} 条, {r['method']})")
            print(f"         目标 F1≥{r['target_f1']:.2f} -> {'✅ 达标' if r['target_met'] else '⚠️ 未达标'}")
        elif r["task"] == "resume":
            print(f"简历提取  P={r['precision']:.4f} R={r['recall']:.4f} F1={r['f1']:.4f} "
                  f"({r['samples']} 条, {r['method']})")
            print(f"         目标 F1≥{r['target_f1']:.2f} -> {'✅ 达标' if r['target_met'] else '⚠️ 未达标'}")
        else:
            print(f"人岗匹配  Spearman={r['spearman']:.4f} Accuracy={r['accuracy']:.4f} ({r['method']})")
            print(f"         目标 Acc≥{r['target_accuracy']:.2f} -> {'✅ 达标' if r['target_met'] else '⚠️ 未达标'}")
    print(f"报告已写入: {report_path.relative_to(_BACKEND_DIR)}")


if __name__ == "__main__":
    main()
