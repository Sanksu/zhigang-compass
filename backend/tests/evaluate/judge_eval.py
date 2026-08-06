"""LLM-as-judge 跨模型交叉验证。

用独立的 LLM（如 GPT-4）评估本项目 LLM（如讯飞星火）的输出质量，
消除自评偏差。目标：一致性 ≥ 0.85
"""

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent  # tests/evaluate/
_BACKEND_DIR = _HERE.parents[1]          # backend/
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_BACKEND_DIR))

from run_baseline import load_golden_set  # noqa: E402


def judge_jd_extraction(pred: dict, gold: dict, judge_llm) -> dict:
    """用 judge LLM 评估抽取结果质量。

    Args:
        pred: 本项目 LLM 的抽取结果
        gold: 人工标注的黄金集
        judge_llm: 独立 LLM 客户端（如 GPT-4）
    Returns:
        {score, reasoning, missing_fields, hallucinated_fields}
    """
    # TODO: M4 阶段实现
    ...


def compute_agreement(judge_scores: list[dict]) -> float:
    """计算 judge 与本项目评分的一致性（Cohen's Kappa）。"""
    # TODO: M4 阶段实现
    ...


def main():
    golden = load_golden_set(str(_BACKEND_DIR / "data" / "golden_set" / "jd_golden_100.jsonl"))

    if not golden:
        print("[SKIP] 黄金集不存在")
        return

    # TODO: 加载 judge LLM、运行评估、输出报告
    print("[TODO] LLM-as-judge 评估待 M4 阶段实现")


if __name__ == "__main__":
    main()
