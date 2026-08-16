"""BT 匹配黄金集 v2 构造（AL-M5-02，2026-08-16 决策执行）。

方案：temp/BT黄金集补标注方案_20260813.md。用户决策（08-16）：
- nice 口径 B：LLM 重抽 100 条 raw_text，抽取 necessity 独立成 must/nice
  （不以 gold_skills 为准；抽取失败回退 gold_skills 为 must）
- 候选侧弱监督注入（正例年限达标 + 负例年限不足，专为 exp 维度造区分）
- 规模 400 对（每岗位 1 正 + 3 负；44 个有年限要求岗位的负例③为"技能匹配
  但年限不足"，56 个经验不限岗位负例③为第三个技能不匹配负例）
- v1 并存：输出 golden_set_match_v2.jsonl，golden_set_match.jsonl 不动

v2 记录结构（exp 维度可学）：
{position_id, position_skills_must, position_skills_nice, required_years,
 candidate_skills, candidate_total_years, candidate_proficiency, label}

用法（cwd=backend）：
    python -m scripts.build_match_golden_v2      # 生成 v2（LLM 重抽约 100 次调用）
    python -m scripts.build_match_golden_v2 --no-llm   # 跳过重抽（回退 gold_skills）
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.logging import setup_logging

logger = setup_logging("build_match_golden_v2")

_GOLDEN_JD = ROOT / "data" / "golden_set" / "jd_golden_100.jsonl"
_OUTPUT = ROOT / "data" / "golden_set" / "golden_set_match_v2.jsonl"

SEED = 42
PAIRS_PER_POSITION = 4      # 1 正 + 3 负
POSITIVE_KEEP_RATIO = 0.6   # 正样本保留 must ≥60%（与 v1 口径一致）
BASELINE_YEARS = 2          # 经验不限岗位候选基线年限


def _load_golden() -> list[dict]:
    return [
        json.loads(line)
        for line in _GOLDEN_JD.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _extract_necessity(items: list[dict], use_llm: bool) -> list[tuple[list[str], list[str]]]:
    """每条 JD 的 (must, nice)。

    口径 B（抽取独立）：LLM 重抽 necessity；抽取失败/无 must 时回退 gold_skills
    为 must（gold_bonus_skills 为空属标注遗留，不回退）。
    """
    result: list[tuple[list[str], list[str]]] = []
    if use_llm:
        from app.services.extraction.jd_extractor import JDExtractor

        extractor = JDExtractor()
    for i, it in enumerate(items):
        must, nice = [], []
        if use_llm:
            try:
                r = extractor.extract(
                    it.get("raw_text") or it.get("original_raw_text") or "",
                    title_hint=it.get("title") or "",
                )
                # necessity 在 requirements（REQUIRESRelation.skill_name），
                # skills 为 SkillExtracted（无 necessity 字段）
                must = [rel.skill_name for rel in r.requirements if rel.necessity == "must"]
                nice = [rel.skill_name for rel in r.requirements if rel.necessity == "nice"]
            except Exception as exc:  # LLM 调用失败不阻断整体
                logger.warning("jd %s 重抽失败: %s", it.get("id"), str(exc)[:120])
        if not must:
            must = [s for s in (it.get("gold_skills") or []) if s.strip()]
        result.append((must, nice))
        if (i + 1) % 20 == 0:
            logger.info("  重抽进度 %s/100", i + 1)
    return result


def _required_years(item: dict) -> int:
    exp = item.get("gold_experience") or {}
    return int(exp.get("min_years") or 0)


def _build_pairs(items: list[dict], necessity: list[tuple[list[str], list[str]]]) -> list[dict]:
    """每岗位 4 对：1 正 + 3 负（有年限岗位负例③为年限不足）。"""
    rng = random.Random(SEED)
    records: list[tuple[str, list[str], list[str], int]] = []
    for it, (must, nice) in zip(items, necessity):
        if must:
            records.append((it["id"], must, nice, _required_years(it)))

    # 他岗技能池（负例① 技能不匹配用，与目标岗重叠 ≤1）
    all_must = [m for _, m, _, _ in records]

    pairs: list[dict] = []
    for pos_id, must, nice, req_years in records:
        # 正样本：must 子集（≥60%）+ 年限达标（无要求给基线 2 年）+ 熟练度 2-3
        keep = max(1, int(len(must) * POSITIVE_KEEP_RATIO))
        pairs.append({
            "position_id": pos_id,
            "position_skills_must": must,
            "position_skills_nice": nice,
            "required_years": req_years,
            "candidate_skills": rng.sample(must, keep),
            "candidate_total_years": max(req_years, BASELINE_YEARS),
            "candidate_proficiency": rng.choice([2, 3]),
            "label": 1,
        })
        # 负样本① ②：他岗技能子集（重叠 ≤1），年限满足
        negs = 0
        for _ in range(20):  # 最多试 20 次找足 2 个不匹配样本
            other = rng.choice(all_must)
            if other == must:
                continue
            subset = rng.sample(other, max(1, len(other) // 2))
            if len(set(subset) & set(must)) <= 1:
                pairs.append({
                    "position_id": pos_id,
                    "position_skills_must": must,
                    "position_skills_nice": nice,
                    "required_years": req_years,
                    "candidate_skills": subset,
                    "candidate_total_years": max(req_years, BASELINE_YEARS),
                    "candidate_proficiency": 2,
                    "label": 0,
                })
                negs += 1
                if negs == 2:
                    break
        # 负样本③：有年限岗位 → 技能匹配但年限不足；经验不限 → 第三个技能不匹配
        if req_years >= 1:
            pairs.append({
                "position_id": pos_id,
                "position_skills_must": must,
                "position_skills_nice": nice,
                "required_years": req_years,
                "candidate_skills": rng.sample(must, keep),
                "candidate_total_years": max(0, req_years - 1),
                "candidate_proficiency": 1,
                "label": 0,
            })
        else:
            for _ in range(20):
                other = rng.choice(all_must)
                if other == must:
                    continue
                subset = rng.sample(other, max(1, len(other) // 2))
                if len(set(subset) & set(must)) <= 1:
                    pairs.append({
                        "position_id": pos_id,
                        "position_skills_must": must,
                        "position_skills_nice": nice,
                        "required_years": req_years,
                        "candidate_skills": subset,
                        "candidate_total_years": BASELINE_YEARS,
                        "candidate_proficiency": 2,
                        "label": 0,
                    })
                    break
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BT 匹配黄金集 v2 构造")
    parser.add_argument("--no-llm", action="store_true", help="跳过 LLM 重抽（回退 gold_skills）")
    args = parser.parse_args(argv)

    items = _load_golden()
    logger.info("JD 黄金集 %s 条", len(items))
    necessity = _extract_necessity(items, use_llm=not args.no_llm)
    must_filled = sum(1 for m, _ in necessity if m)
    nice_filled = sum(1 for _, n in necessity if n)
    logger.info("重抽完成: must 非空 %s/100，nice 非空 %s/100", must_filled, nice_filled)

    pairs = _build_pairs(items, necessity)
    labels = {0: 0, 1: 0}
    for p in pairs:
        labels[p["label"]] += 1
    exp_pairs = sum(1 for p in pairs if p["required_years"] >= 1 and p["label"] == 0
                    and p["candidate_total_years"] < p["required_years"])
    logger.info("v2 对 %s 条: label=%s；年限不足负例 %s 条", len(pairs), labels, exp_pairs)

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with _OUTPUT.open("w", encoding="utf-8") as fh:
        for p in pairs:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    logger.info("已写入: %s", _OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
