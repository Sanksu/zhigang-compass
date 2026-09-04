# -*- coding: utf-8 -*-
"""education 匹配语义金标生成（2026-09-01 域治理可解释性补充）。

将学历匹配的语义契约冻结为数据：岗位学历要求 × 候选人学历的全矩阵
期望得分，由负责人 2026-09-01 拍板的教育近似规则导出
（engine._education_score：候选人层级 ≥ 要求 → 1.0；低一级 → 0.5；
其余 → 0.0；任一侧无法识别层级 → null=不判分）。

用途：回归验收——防止 _edu_level 关键词表/_education_score 规则的
未来改动静默破坏学历匹配语义（真实词表覆盖：本科 4236/硕士 678/
大专 511/高中 217/博士 168/不限 116/中专 19/初中 9/中专中技 2）。
期望值由规则语义人工填入（非实现计算），实现回归时对拍。

输出：data/golden_set/education_semantics_v1.jsonl
"""

from __future__ import annotations

import json
from pathlib import Path

# 学历层级（与 engine._EDU_LEVELS 一致的语义序，None=无法识别）
# "中专/中技"复合词：实现按 _edu_level 子串匹配命中"中专"→0，契约同口径
LEVELS = {"博士": 4, "硕士": 3, "本科": 2, "大专": 1, "高中": 0,
          "中专": 0, "初中": 0, "中专/中技": 0}

# 岗位要求档位（真实抽取词表全覆盖，含边界："不限"=无要求、"中专/中技"=复合词）
JD_REQUIREMENTS = ["博士", "硕士", "本科", "大专", "高中", "中专", "初中", "不限", "中专/中技"]
# 候选人档位（简历解析产词表）
CANDIDATE_LEVELS = ["博士", "硕士", "本科", "大专", "高中", "中专", "初中", "不限", None]


def expected_score(jd_req: str | None, cand: str | None) -> float | None:
    """教育近似规则（语义契约，人工拍板版）。

    候选人层级 ≥ 要求 → 1.0；低一级 → 0.5；更低 → 0.0。
    任一侧无法映射层级（含"不限"、未知词、None）→ null（不判分）。
    """
    req_level = LEVELS.get(jd_req)
    cand_level = LEVELS.get(cand)
    if req_level is None or cand_level is None:
        return None
    if cand_level >= req_level:
        return 1.0
    if cand_level == req_level - 1:
        return 0.5
    return 0.0


def main() -> None:
    out_path = Path(__file__).resolve().parents[1] / "data" / "golden_set" / "education_semantics_v1.jsonl"
    rows: list[dict] = [
        {
            "_meta": {
                "file": "education_semantics_v1",
                "version": "2026-09-01.v1",
                "status": "frozen（负责人 2026-09-01 拍板教育近似规则后由规则语义冻结）",
                "口径": "学历匹配语义契约：expected 为按规则人工推导的期望得分，"
                       "与 engine._education_score/_edu_level 的实现对拍；"
                       "null=任一侧无法映射层级（不判分）",
                "覆盖": "岗位要求 9 档（真实抽取词表全量）× 候选人 8 档 + 边界",
            }
        }
    ]
    for jd_req in JD_REQUIREMENTS:
        for cand in CANDIDATE_LEVELS:
            exp = expected_score(jd_req, cand)
            rows.append({
                "jd_requirement": jd_req,
                "candidate_level": cand,
                "expected_score": exp,
            })
    with out_path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n = sum(1 for r in rows if not any(k.startswith("_") for k in r))
    print(f"生成 {out_path.name}：{n} 条用例（含 {len(JD_REQUIREMENTS)} 个岗位要求档位）")


if __name__ == "__main__":
    main()
