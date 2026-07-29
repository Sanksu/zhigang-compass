"""真实 LLM 端到端评估。

调用算法管线的真实 LLM 抽取 + 匹配，评估三项准确率。
目标：F1 ≥ 0.90
"""

import json
import argparse
from pathlib import Path
from run_baseline import load_golden_set, compute_f1


def evaluate_jd_extraction(golden_set: list[dict]) -> dict:
    """JD 解析端到端评估（调用算法管线）。"""
    # TODO: M3 阶段实现，调用 algorithm/extraction/jd_extract.py
    ...


def evaluate_resume_extraction(golden_set: list[dict]) -> dict:
    """简历提取端到端评估。"""
    # TODO: M3 阶段实现，调用 algorithm/extraction/resume_eval.py
    ...


def evaluate_matching(golden_set: list[dict]) -> dict:
    """人岗匹配端到端评估。"""
    # TODO: M3 阶段实现，调用 algorithm/matching/scorer.py
    ...


def main():
    parser = argparse.ArgumentParser(description="真实 LLM 端到端评估")
    parser.add_argument("--task", choices=["jd", "resume", "match", "all"], default="all")
    args = parser.parse_args()

    eval_dir = Path(__file__).parent

    if args.task in ("jd", "all"):
        golden = load_golden_set(str(eval_dir / "golden_set_jd.jsonl"))
        if golden:
            results = evaluate_jd_extraction(golden)
            print(f"[JD] F1: {results.get('f1', 'N/A')}")

    if args.task in ("resume", "all"):
        golden = load_golden_set(str(eval_dir / "golden_set_resume.jsonl"))
        if golden:
            results = evaluate_resume_extraction(golden)
            print(f"[Resume] F1: {results.get('f1', 'N/A')}")

    if args.task in ("match", "all"):
        golden = load_golden_set(str(eval_dir / "golden_set_match.jsonl"))
        if golden:
            results = evaluate_matching(golden)
            print(f"[Match] Accuracy: {results.get('accuracy', 'N/A')}")


if __name__ == "__main__":
    main()
