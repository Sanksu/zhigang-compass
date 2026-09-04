# -*- coding: utf-8 -*-
"""BT 四维重调对照实验（2026-09-01 education 进评分实证）。

在 golden_set_match_v3（含学历维度）上系统对比：
- 基线三维（w_edu=None，configs/match_weights.json 现行 BT v3）
- 四维变体（超参搜索 w_edu ∈ {0.05,0.1,0.15,0.2}，对 base_total 凸组合）

目标：判定 education 进评分是否提升分类准确率（Acc，判定线
MATCH_CLASSIFY_THRESHOLD=0.57）。若四维 Acc 显著优于基线三维，且 Spearman
不劣化，则 education 值得接入；否则维持"仅雷达展示维"。

复用 scripts/tune_match_weights 的 build_position/build_candidate/
evaluate_pairs（加学历透传的子类），与生产评分引擎 score_position 同口径。

用法（cwd=backend）：
    python -m scripts.tune_match_weights_fourdim        # 基线上叠加 w_edu 扫描
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.logging import setup_logging
from app.services.matching.schemas import CandidateProfile, CandidateSkill, Necessity, PositionProfile, SkillRequirement
from app.services.matching.weights import load_sim_threshold, load_weights
from scripts.tune_match_weights import MATCH_CLASSIFY_THRESHOLD, load_pairs

logger = setup_logging("tune_match_weights_fourdim")

_V3 = ROOT / "data" / "golden_set" / "golden_set_match_v3.jsonl"


def build_position_v3(p: dict) -> tuple[list, list, float | None, str | None]:
    """v3 岗位侧：技能要求 + 学历要求（position_education）。"""
    musts = [
        SkillRequirement(skill_id=s, skill_name=s, necessity=Necessity.MUST, weight=1.0)
        for s in p["position_skills_must"]
    ]
    nices = [
        SkillRequirement(skill_id=s, skill_name=s, necessity=Necessity.NICE, weight=1.0)
        for s in p.get("position_skills_nice") or []
    ]
    return musts, nices, p.get("required_years") or None, p.get("position_education")


def build_candidate_v3(p: dict) -> CandidateProfile:
    """v3 候选人侧：技能 + 年限 + 学历（探针口径，同 tune 脚本）。

    用 v3 行字面 candidate_skills/years/proficiency/education 直接构造
    CandidateProfile——不走 loaders.build_candidate（生产口径需
    ResumeCache.parsed_data，探针行无此结构，会丢失技能/学历）。
    """
    return CandidateProfile(
        user_id="eval",
        skills=[
            CandidateSkill(skill_id=s, skill_name=s,
                           proficiency=p.get("candidate_proficiency", 2))
            for s in p["candidate_skills"]
        ],
        total_years=p.get("candidate_total_years", 5.0),
        education_level=p.get("candidate_education"),
    )


def evaluate_v3(pairs, weights, w_edu, semantic, sim_threshold) -> dict:
    """v3 四维评分 + Acc/Spearman。w_edu=None 时引擎侧不启用教育维（基线）。"""
    from scipy.stats import spearmanr

    from app.services.matching.engine import score_position

    scores, labels = [], []
    for p in pairs:
        musts, nices, req_years, req_edu = build_position_v3(p)
        position = PositionProfile(
            position_id=p["position_id"], name=p["position_id"],
            must_skills=musts, nice_skills=nices, required_years=req_years,
            required_education=req_edu,
        )
        candidate = build_candidate_v3(p)
        # 显式传 w_edu（引擎基线 load_edu_weight 从配置读，此处测试注入）
        r = score_position(
            candidate, position, weights=weights, semantic=semantic,
            sim_threshold=sim_threshold, w_edu_override=w_edu,
        )
        scores.append(r.total_score)
        labels.append(p["label"])

    hits = sum(1 for s, lb in zip(scores, labels) if (s >= MATCH_CLASSIFY_THRESHOLD) == (lb == 1))
    accuracy = hits / len(scores) if scores else 0.0
    corr = spearmanr(scores, labels).statistic
    return {"spearman": float(corr) if corr == corr else 0.0,
            "accuracy": accuracy, "n": len(scores)}


def main(argv: list[str] | None = None) -> int:
    pairs = load_pairs(_V3)
    logger.info("v3 黄金集 %s 对", len(pairs))
    w_must, w_nice, w_exp = load_weights()
    threshold = load_sim_threshold()
    logger.info("基线三维权重: must=%.3f nice=%.3f exp=%.3f 阈值=%.3f", w_must, w_nice, w_exp, threshold)

    sem = None
    try:
        from app.services.matching.semantic import SkillEmbedder
        sem = SkillEmbedder.get()
    except Exception:
        logger.warning("SBERT 不可用，纯规则口径")

    # 基线三维（w_edu=None）
    base = evaluate_v3(pairs, (w_must, w_nice, w_exp), None, sem, threshold)
    logger.info("基线三维: Acc=%.4f Spearman=%.4f", base["accuracy"], base["spearman"])

    # 四维扫描
    for w_edu in (0.05, 0.1, 0.15, 0.2):
        r = evaluate_v3(pairs, (w_must, w_nice, w_exp), w_edu, sem, threshold)
        delta = r["accuracy"] - base["accuracy"]
        logger.info("w_edu=%.2f: Acc=%.4f (Δ%+.4f) Spearman=%.4f",
                    w_edu, r["accuracy"], delta, r["spearman"])
        print(f"w_edu={w_edu:.2f}: Acc={r['accuracy']:.4f} (Δ{delta:+.4f}) Spear={r['spearman']:.4f}")

    print(f"基线:      Acc={base['accuracy']:.4f} Spear={base['spearman']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
