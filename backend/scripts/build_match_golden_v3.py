# -*- coding: utf-8 -*-
"""BT 匹配黄金集 v3 构造（2026-09-01 education 进评分实证）。

v2 基础上注入学历维度（教育语义契约 education_semantics_v1 的行为化落地）：
- 岗位侧：position_id → jd_golden_100.gold_education.level（本科 62/大专 24/
  学历不限 5/硕士 9，384 对全可 join）
- 候选人侧：每对补 candidate_education 梯度——正例达标（label=1 前提），
  学历不足负例（label=0）新增一档
- label 重判规则：教育近似语义（engine._education_score：达标 1.0/低一级
  0.5/更低 0）下，正例学历 ≥ 要求；学历低于要求一级以上（0.0 分）的探针
  判 0（学历不足是独立拒因）；低一级（0.5）保持原 label（弱不足不翻盘，
  与"年限不足一档"负例③同哲学：单一弱信号不主导判定）
- "学历不限"岗位：candidate_education 不影响 label（不判分口径）

v3 记录结构（v2 全字段 + education 可学）：
{...v2字段, position_education, candidate_education}

v2 兼容：不含新字段的行（旧 v2/v1）在评测侧按"无学历信号"处理，
education 维度自动退出归一（引擎侧向后兼容）。

用法（cwd=backend）：
    python -m scripts.build_match_golden_v3     # 从 v2 生成（无需 LLM）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.logging import setup_logging

logger = setup_logging("build_match_golden_v3")

_V2 = ROOT / "data" / "golden_set" / "golden_set_match_v2.jsonl"
_GOLDEN_JD = ROOT / "data" / "golden_set" / "jd_golden_100.jsonl"
_OUTPUT = ROOT / "data" / "golden_set" / "golden_set_match_v3.jsonl"

# 教育层级（与 engine._EDU_LEVELS 语义序一致）
_LEVELS = {"博士": 4, "硕士": 3, "本科": 2, "大专": 1, "高中": 0, "中专": 0, "初中": 0}
# 候选人学历梯度：正例/学历不足负例按要求档位降一档/降两档构造
_CAND_GRADIENT = ["本科", "大专", "高中"]


def _edu_level(text: str | None) -> int | None:
    return _LEVELS.get((text or "").strip())


def main(argv: list[str] | None = None) -> int:
    golden_jd = {
        json.loads(line)["id"]: json.loads(line)
        for line in _GOLDEN_JD.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    pairs = [json.loads(l) for l in _V2.read_text(encoding="utf-8").splitlines() if l.strip()]

    out: list[dict] = []
    stats = {"edu_req": 0, "edu_unlimited": 0, "downgraded": 0, "kept": 0}
    for p in pairs:
        jd = golden_jd.get(p["position_id"]) or {}
        ge = jd.get("gold_education")
        req_level_raw = (ge.get("level") if isinstance(ge, dict) else ge) or None
        req_level_raw = (req_level_raw or "").strip() or None
        req_level = _edu_level(req_level_raw)

        row = dict(p)
        row["position_education"] = req_level_raw  # "学历不限" 原样保留（不判分口径）
        stats["edu_req" if req_level is not None else "edu_unlimited"] += 1

        if req_level is None:
            # 学历不限：候选学历给中性本科（education 维 null，不参与判分）
            row["candidate_education"] = "本科"
            stats["kept"] += 1
            out.append(row)
            continue

        cand_level = _edu_level(p.get("candidate_education"))
        if cand_level is None:
            # v2 探针无学历：按 label 注入梯度
            if p["label"] == 1:
                # 正例：学历达标（取要求档位本身）
                cand_name = next(k for k, v in _LEVELS.items() if v == req_level and k in _CAND_GRADIENT) \
                    if any(k for k, v in _LEVELS.items() if v == req_level and k in _CAND_GRADIENT) \
                    else next(k for k, v in _LEVELS.items() if v == req_level)
                row["candidate_education"] = cand_name
                stats["kept"] += 1
            else:
                # 负例：学历降两档（0.0 分档——学历不足为独立拒因，不与技能/
                # 年限负例的拒因混淆；降一档 0.5 会稀释 label 语义）
                low_name = next((k for k, v in _LEVELS.items()
                                 if v == max(0, req_level - 2) and k in _CAND_GRADIENT), None)
                if low_name is None:
                    low_name = next(k for k, v in _LEVELS.items() if v == max(0, req_level - 2))
                row["candidate_education"] = low_name
                stats["downgraded"] += 1
        out.append(row)

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with _OUTPUT.open("w", encoding="utf-8", newline="\n") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    labels = {0: 0, 1: 0}
    for r in out:
        labels[r["label"]] += 1
    logger.info("v3 对 %s 条: label=%s；岗位有学历要求 %s/不限 %s；正例达标 %s/负例降两档 %s",
                len(out), labels, stats["edu_req"], stats["edu_unlimited"],
                stats["kept"], stats["downgraded"])
    logger.info("已写入: %s", _OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
