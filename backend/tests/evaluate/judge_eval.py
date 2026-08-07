"""LLM-as-judge 跨模型交叉验证（TE-M2-03，设计文档 §13.3）。

用独立的 LLM 评估本项目 LLM 的输出质量，消除自评偏差。目标：一致性 ≥ 0.85。

评测流程：
1. 加载黄金集（jd_golden_100.jsonl）
2. 对每条 JD，本项目 LLM 抽取技能（JDExtractor.extract，无 LLM 时规则兜底）
3. judge LLM 评估抽取结果 vs 黄金集标注（judge_jd_extraction）
4. 计算 judge 评分与本项目 F1 评分的一致性（Cohen's Kappa，compute_agreement）

无 judge LLM API Key 时跳过并注明，不伪造结果。

用法：
    uv run python tests/evaluate/judge_eval.py                       # 默认 judge provider
    uv run python tests/evaluate/judge_eval.py --limit 10            # 小批量冒烟
    uv run python tests/evaluate/judge_eval.py --judge-model gpt-4o  # 指定 judge 模型

环境变量：
    LLM_JUDGE_API_KEY   judge LLM 的 API Key（必需，无则跳过）
    LLM_JUDGE_BASE_URL  judge LLM 的 base_url（默认 https://api.openai.com/v1）
    LLM_JUDGE_MODEL     judge LLM 的模型名（默认 gpt-4o）
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from pydantic import BaseModel, Field

_HERE = Path(__file__).resolve().parent
_BACKEND_DIR = _HERE.parents[1]
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_BACKEND_DIR))

from run_baseline import compute_f1, keyword_match, load_golden_set  # noqa: E402

_GOLDEN_JD = _BACKEND_DIR / "data" / "golden_set" / "jd_golden_100.jsonl"


class JudgeResult(BaseModel):
    """judge LLM 评估结果（Pydantic 强校验，幻觉防控第一道防线）。"""

    score: float = Field(ge=0.0, le=1.0, description="抽取质量评分 0-1")
    reasoning: str = Field(description="评分理由")
    missing_fields: list[str] = Field(default_factory=list, description="漏抽的技能")
    hallucinated_fields: list[str] = Field(default_factory=list, description="幻觉/误抽的技能")


_JUDGE_SYSTEM_PROMPT = """你是一个严格的技术评测专家。你的任务是评估 JD 技能抽取结果的质量。

给定：
- gold_skills: 黄金集标注的技能列表
- pred_skills: 系统抽取的技能列表

评估维度：
1. score: 抽取质量评分（0.0-1.0），1.0 表示完全正确
2. reasoning: 评分理由（简要说明扣分原因）
3. missing_fields: 漏抽的技能（gold_skills 有但 pred_skills 没有）
4. hallucinated_fields: 幻觉/误抽的技能（pred_skills 有但 gold_skills 没有，且不属于该 JD）

仅返回 JSON，格式：{"score": 0.0, "reasoning": "...", "missing_fields": [...], "hallucinated_fields": [...]}"""


class OpenAIJudgeLLM:
    """OpenAI 兼容 API 的 judge LLM 客户端。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def chat(self, system: str, user: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
        )
        return resp.choices[0].message.content


def _parse_json(raw: str) -> dict:
    """容错解析 LLM 返回的 JSON（可能包裹在 markdown 代码块中）。"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"judge LLM 返回非 JSON: {raw[:200]}")


def judge_jd_extraction(pred: dict, gold: dict, judge_llm) -> dict:
    """用 judge LLM 评估抽取结果质量。

    Args:
        pred: 本项目 LLM 的抽取结果，含 skills 列表（字符串）
        gold: 人工标注的黄金集，含 gold_skills 列表
        judge_llm: 独立 LLM 客户端，需提供 chat(system, user) -> str 方法
    Returns:
        {score, reasoning, missing_fields, hallucinated_fields}
    """
    pred_skills = pred.get("skills", [])
    gold_skills = gold.get("gold_skills", [])

    user_msg = json.dumps(
        {"gold_skills": gold_skills, "pred_skills": pred_skills},
        ensure_ascii=False,
    )

    raw = judge_llm.chat(_JUDGE_SYSTEM_PROMPT, user_msg)
    data = _parse_json(raw)
    result = JudgeResult(**data)
    return {
        "score": result.score,
        "reasoning": result.reasoning,
        "missing_fields": result.missing_fields,
        "hallucinated_fields": result.hallucinated_fields,
    }


def compute_agreement(judge_scores: list[dict]) -> float:
    """计算 judge 与本项目评分的一致性（Cohen's Kappa）。

    judge_scores 中每项含 score（judge 评分，0-1）和 project_score（本项目 F1，0-1）。
    二值化阈值 0.5：>= 0.5 视为"合格"，< 0.5 视为"不合格"。

    Returns:
        Cohen's Kappa 值（-1 到 1），样本不足时返回 0.0
    """
    if len(judge_scores) < 2:
        return 0.0

    n = len(judge_scores)
    judge_labels = [1 if s["score"] >= 0.5 else 0 for s in judge_scores]
    project_labels = [1 if s.get("project_score", 0) >= 0.5 else 0 for s in judge_scores]

    po = sum(1 for j, p in zip(judge_labels, project_labels) if j == p) / n

    p_judge_pos = sum(judge_labels) / n
    p_proj_pos = sum(project_labels) / n
    pe = p_judge_pos * p_proj_pos + (1 - p_judge_pos) * (1 - p_proj_pos)

    if pe == 1.0:
        return 1.0
    return (po - pe) / (1 - pe)


def main():
    parser = argparse.ArgumentParser(description="LLM-as-judge 跨模型交叉验证")
    parser.add_argument("--limit", type=int, default=None, help="仅评估前 N 条（冒烟测试）")
    parser.add_argument(
        "--judge-model",
        default=None,
        help="judge LLM 模型名（默认从环境变量 LLM_JUDGE_MODEL 读取）",
    )
    args = parser.parse_args()

    api_key = os.environ.get("LLM_JUDGE_API_KEY") or os.environ.get("LLM_API_KEY")
    if not api_key:
        print("[SKIP] 未配置 judge LLM API Key（设置 LLM_JUDGE_API_KEY 或 LLM_API_KEY 环境变量）")
        return

    base_url = os.environ.get("LLM_JUDGE_BASE_URL") or os.environ.get(
        "LLM_BASE_URL", "https://api.openai.com/v1"
    )
    model = args.judge_model or os.environ.get("LLM_JUDGE_MODEL", "gpt-4o")

    golden = load_golden_set(str(_GOLDEN_JD))
    if not golden:
        print("[SKIP] 黄金集不存在")
        return

    samples = golden[: args.limit] if args.limit else golden

    from app.services.extraction.jd_extractor import JDExtractor

    extractor = JDExtractor()
    judge_llm = OpenAIJudgeLLM(api_key, base_url, model)

    judge_scores: list[dict] = []
    errors = 0
    for i, item in enumerate(samples, 1):
        text = item.get("raw_text") or ""
        gold = item.get("gold_skills") or []
        if not text or not gold:
            continue
        try:
            result = extractor.extract(text)
            pred_skills = [s.name for s in result.skills]
        except Exception as e:
            errors += 1
            print(f"  [{i:>3}] EXTRACT ERR: {e}")
            continue

        tp, fp, fn = keyword_match(pred_skills, gold)
        _, _, project_f1 = compute_f1(tp, fp, fn)

        try:
            judge_result = judge_jd_extraction(
                {"skills": pred_skills},
                {"gold_skills": gold},
                judge_llm,
            )
        except Exception as e:
            errors += 1
            print(f"  [{i:>3}] JUDGE ERR: {e}")
            continue

        judge_scores.append(
            {
                "score": judge_result["score"],
                "project_score": project_f1,
                "missing_fields": judge_result["missing_fields"],
                "hallucinated_fields": judge_result["hallucinated_fields"],
            }
        )

        if i % 10 == 0 or i == len(samples):
            print(f"  ... {i}/{len(samples)} 已处理")

    if not judge_scores:
        print("[SKIP] 无有效评测结果")
        return

    kappa = compute_agreement(judge_scores)
    avg_judge_score = sum(s["score"] for s in judge_scores) / len(judge_scores)
    avg_project_f1 = sum(s["project_score"] for s in judge_scores) / len(judge_scores)

    print("=" * 56)
    print("LLM-as-judge 交叉验证报告（TE-M2-03）")
    print("=" * 56)
    print(f"judge 模型: {model}")
    print(f"样本数: {len(judge_scores)}（错误 {errors}）")
    print(f"judge 平均评分: {avg_judge_score:.4f}")
    print(f"项目平均 F1:    {avg_project_f1:.4f}")
    print(f"Cohen's Kappa:  {kappa:.4f}")
    print(f"目标一致性 ≥ 0.85 -> {'达标' if kappa >= 0.85 else '未达标'}")


if __name__ == "__main__":
    main()
