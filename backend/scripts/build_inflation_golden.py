"""通胀检测合成标注集生成（DA-M3-04，设计文档 §4.8）。

场景由业务意图判定真值（gold_label），不依赖检测器输出，避免标注与模型
循环论证。正样本覆盖四维单维度溢出 + 组合 + 设计文档典型样本；负样本覆盖
各级别合理基线 + 单维略超但整体合理的边界样本。

用法：
    uv run python scripts/build_inflation_golden.py
    uv run python scripts/build_inflation_golden.py --dry-run   # 仅打印统计不落盘
"""

import argparse
import json
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_OUTPUT = _BACKEND_DIR / "data" / "golden_set" / "golden_set_inflation.jsonl"

# 场景字段：(id, job_level, min_years, skill_count, expert_count, education, gold_label, scenario)
# gold_label ∈ normal / mild_inflation / severe_inflation；is_inflation = label != normal
#
# 真值口径（业务上无争议的明确通胀）：强维度溢出（如初级 10 年经验/初级 10 项技能/
# 初级 3 项精通/初级博士）与组合溢出；边界情形（单维轻度溢出：初级 1 项精通、初级要求
# 硕士、中级要求博士等）归入负样本——这些在招聘市场常见，不构成"虚高"。

_SCENARIOS: list[tuple] = [
    # ── 设计文档 §4.8 典型样本 ──
    ("inf_001", "初级", 10, 8, 3, "硕士", "severe_inflation", "初级岗要求10年大模型经验（设计文档典型样本）"),
    ("inf_002", "专家", 20, 30, 15, "博士", "severe_inflation", "专家岗要求20年+30技能+15项精通，全面通胀"),
    # ── 经验维度强溢出（明确通胀）──
    ("inf_003", "初级", 6, 3, 0, "本科", "mild_inflation", "初级岗要求6年经验（ceiling 3 翻倍）"),
    ("inf_004", "初级", 8, 4, 0, "本科", "severe_inflation", "初级岗要求8年经验"),
    ("inf_005", "中级", 8, 6, 1, "本科", "mild_inflation", "中级岗要求8年经验（ceiling 5 超3年）"),
    ("inf_006", "中级", 10, 6, 1, "本科", "severe_inflation", "中级岗要求10年经验"),
    ("inf_007", "高级", 13, 8, 2, "本科", "severe_inflation", "高级岗要求13年经验"),
    ("inf_008", "资深", 15, 12, 4, "硕士", "severe_inflation", "资深岗要求15年经验（ceiling 10 超5年）"),
    ("inf_009", "专家", 17, 18, 8, "博士", "mild_inflation", "专家岗要求17年经验（ceiling 12 超5年）"),
    # ── 技能数量维度强溢出（明确通胀）──
    ("inf_010", "初级", 2, 10, 0, "本科", "severe_inflation", "初级岗要求10项技能"),
    ("inf_011", "中级", 4, 13, 1, "本科", "severe_inflation", "中级岗要求13项技能（ceiling 8 超5项）"),
    ("inf_012", "高级", 7, 18, 2, "本科", "severe_inflation", "高级岗要求18项技能（ceiling 12 超6项）"),
    ("inf_013", "资深", 9, 21, 4, "硕士", "severe_inflation", "资深岗要求21项技能（ceiling 15 超6项）"),
    ("inf_014", "专家", 10, 25, 6, "博士", "mild_inflation", "专家岗要求25项技能（ceiling 20 超5项）"),
    # ── 技能深度维度强溢出（明确通胀）──
    ("inf_015", "初级", 2, 4, 3, "本科", "severe_inflation", "初级岗要求3项精通"),
    ("inf_016", "中级", 4, 7, 5, "本科", "severe_inflation", "中级岗要求5项精通"),
    ("inf_017", "高级", 7, 10, 7, "本科", "severe_inflation", "高级岗要求7项精通（ceiling 4 超3项）"),
    ("inf_018", "资深", 9, 13, 9, "硕士", "severe_inflation", "资深岗要求9项精通（ceiling 6 超3项）"),
    ("inf_019", "专家", 10, 18, 13, "博士", "severe_inflation", "专家岗要求13项精通（ceiling 10 超3项）"),
    # ── 学历维度强溢出（明确通胀）──
    ("inf_020", "初级", 2, 4, 0, "博士", "severe_inflation", "初级岗要求博士（ceiling 本科）"),
    # ── 组合维度 ──
    ("inf_021", "初级", 6, 9, 1, "硕士", "severe_inflation", "初级岗经验+数量+深度+学历四维均溢出"),
    ("inf_022", "中级", 8, 12, 3, "本科", "mild_inflation", "中级岗经验+数量+深度三维中度溢出"),
    ("inf_023", "初级", 7, 10, 2, "本科", "severe_inflation", "初级岗经验+数量+深度组合溢出"),
    ("inf_024", "高级", 12, 16, 5, "硕士", "mild_inflation", "高级岗经验+数量+深度组合溢出"),
    ("inf_025", "资深", 13, 18, 8, "硕士", "mild_inflation", "资深岗经验+数量+深度组合溢出"),
    ("inf_026", "中级", 9, 11, 4, "博士", "severe_inflation", "中级岗四维组合强溢出"),
    ("inf_027", "初级", 8, 6, 1, "本科", "severe_inflation", "初级岗经验强溢出+其他维中度"),
    ("inf_028", "高级", 15, 14, 6, "硕士", "severe_inflation", "高级岗经验强溢出+深度溢出"),
    # ── 负样本：各级别合理基线 ──
    ("inf_029", "初级", 3, 5, 0, "本科", "normal", "初级岗合理要求"),
    ("inf_030", "初级", 2, 4, 0, "大专", "normal", "初级岗合理要求"),
    ("inf_031", "中级", 5, 8, 2, "本科", "normal", "中级岗合理要求"),
    ("inf_032", "中级", 4, 6, 1, "本科", "normal", "中级岗合理要求"),
    ("inf_033", "高级", 8, 12, 4, "本科", "normal", "高级岗合理要求"),
    ("inf_034", "高级", 7, 10, 3, "硕士", "normal", "高级岗合理要求"),
    ("inf_035", "资深", 10, 15, 6, "硕士", "normal", "资深岗合理要求"),
    ("inf_036", "专家", 12, 20, 10, "博士", "normal", "专家岗合理要求"),
    ("inf_037", "初级", 3, 5, 0, "不限", "normal", "初级岗合理要求"),
    ("inf_038", "高级", 8, 12, 4, "硕士", "normal", "高级岗合理要求"),
    ("inf_039", "专家", 10, 20, 8, "博士", "normal", "专家岗合理要求"),
    ("inf_040", "资深", 11, 15, 6, "硕士", "normal", "资深岗合理要求"),
    ("inf_041", "中级", 7, 8, 2, "本科", "normal", "中级岗合理要求"),
    ("inf_042", "初级", 2, 3, 0, "本科", "normal", "初级岗合理要求"),
    # ── 负样本：单维轻度溢出但市场常见（不构成虚高）──
    ("inf_043", "初级", 5, 5, 0, "本科", "normal", "初级岗5年经验，略超但行业常见"),
    ("inf_044", "中级", 6, 8, 2, "本科", "normal", "中级岗6年经验，略超但行业常见"),
    ("inf_045", "高级", 10, 12, 4, "本科", "normal", "高级岗10年经验，行业常见"),
    ("inf_046", "初级", 3, 6, 0, "本科", "normal", "初级岗6项技能，略超但合理"),
    ("inf_047", "中级", 5, 9, 2, "本科", "normal", "中级岗9项技能，略超但合理"),
    ("inf_048", "高级", 8, 13, 4, "本科", "normal", "高级岗13项技能，略超但合理"),
    ("inf_049", "初级", 3, 5, 1, "本科", "normal", "初级岗1项精通，可接受"),
    ("inf_050", "中级", 5, 8, 3, "本科", "normal", "中级岗3项精通，可接受"),
    ("inf_051", "高级", 8, 12, 5, "硕士", "normal", "高级岗5项精通，可接受"),
    ("inf_052", "初级", 3, 5, 0, "硕士", "normal", "初级岗要求硕士，行业常见"),
    ("inf_053", "资深", 10, 16, 6, "硕士", "normal", "资深岗16项技能，略超但合理"),
    ("inf_054", "初级", 4, 6, 0, "本科", "normal", "初级岗4年+6技能，轻微超但整体合理"),
    ("inf_055", "中级", 4, 7, 1, "博士", "normal", "中级岗要求博士，研究岗常见"),
    ("inf_056", "高级", 7, 10, 2, "博士", "normal", "高级岗要求博士，研究岗常见"),
    ("inf_057", "高级", 8, 14, 4, "本科", "normal", "高级岗14项技能，略超但合理"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="通胀检测合成标注集生成")
    parser.add_argument("--dry-run", action="store_true", help="仅打印统计不落盘")
    args = parser.parse_args()

    records = []
    for sid, level, years, count, expert, edu, label, scenario in _SCENARIOS:
        records.append({
            "id": sid,
            "job_level": level,
            "min_years": years,
            "skill_count": count,
            "expert_level_count": expert,
            "education": edu,
            "gold_label": label,
            "is_inflation": label != "normal",
            "scenario": scenario,
        })

    pos = sum(1 for r in records if r["is_inflation"])
    neg = len(records) - pos
    print(f"通胀标注集: 共 {len(records)} 条（正样本 {pos} / 负样本 {neg}）")

    if args.dry_run:
        return
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    print(f"已写入 {_OUTPUT.relative_to(_BACKEND_DIR)}")


if __name__ == "__main__":
    main()
