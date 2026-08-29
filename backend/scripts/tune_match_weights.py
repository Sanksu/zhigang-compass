"""匹配权重/阈值 Optuna 调优（AL-M3-03，设计文档 9.3）。

在弱监督匹配黄金集（build_match_golden.py 生成）上搜索
(w_must, w_nice, w_exp, sim_threshold)，目标最大化 Spearman 秩相关。

用法：
    uv run python scripts/tune_match_weights.py            # 语义增强调优（SBERT）
    uv run python scripts/tune_match_weights.py --no-semantic   # 纯规则基线调优
    uv run python scripts/tune_match_weights.py --eval-only     # 仅评估当前 configs/match_weights.json
    uv run python scripts/tune_match_weights.py --trials 20     # 控制搜索次数

最优参数写入 `configs/match_weights.json`（幂等覆盖，可审计）。
"""

import argparse
import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("tune_match_weights")

from app.services.matching.schemas import (  # noqa: E402
    CandidateProfile,
    CandidateSkill,
    Necessity,
    PositionProfile,
    SkillRequirement,
)

# 人岗匹配评测的岗位画像来源口径（AGENTS.md §4.1 算法核心，张恺天把关）：
# 评测与生产共用 app.services.matching.engine.score_position，**分歧仅在画像来源**：
#   - 评测（本文件 evaluate_pairs）：PositionProfile 来自黄金集行的
#     position_skills_must/nice 字面值，即该 position_id 对应**单条真实 JD** 的技能
#     要求（golden_set_match_v2.jsonl 由 build_match_golden_v2 逐 JD 抽取），
#     不做"归一化岗位名 → Neo4j Position → 聚合 REQUIRES"的合并。
#   - 生产（loaders._load_positions_uncached）：按归一化岗位聚合所有同名 JD 的
#     REQUIRES（freq>=3），是同名多 JD 的合并画像。
# 该差异为**有意为之**：评测要验证"对真实 JD 的匹配"，而非对归一岗位合并画像的匹配。

_GOLDEN_MATCH = _BACKEND_DIR / "data" / "golden_set" / "golden_set_match.jsonl"
_WEIGHTS_PATH = _BACKEND_DIR / "configs" / "match_weights.json"


def load_pairs(path: Path) -> list[dict]:
    """加载匹配黄金集（JSONL）。"""
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def build_position(p: dict) -> tuple[list[SkillRequirement], list[SkillRequirement], float | None]:
    """从黄金集行构造岗位侧技能要求（真实 JD 口径，非图谱聚合）。

    - v2（golden_set_match_v2.jsonl，BT 补标注 08-16）：position_skills_must/nice
      是该 position_id 对应**单条真实 JD** 经 LLM 重抽（回收 gold_skills）得到的
      must/nice，directly 用作 PositionProfile（真实 JD 画像）。
    - v1（golden_set_match.jsonl）：position_skills 全 must、无 nice。
    两口径均**不**经"归一化岗位名 → Neo4j Position 节点 → 聚合 REQUIRES"。
    """
    if "position_skills_must" in p:
        musts = [
            SkillRequirement(skill_id=s, skill_name=s, necessity=Necessity.MUST, weight=1.0)
            for s in p["position_skills_must"]
        ]
        nices = [
            SkillRequirement(skill_id=s, skill_name=s, necessity=Necessity.NICE, weight=1.0)
            for s in p.get("position_skills_nice") or []
        ]
        return musts, nices, p.get("required_years") or None
    # v1：position_skills 全 must、候选无年限信息（exp 恒满分退化）
    musts = [
        SkillRequirement(skill_id=s, skill_name=s, necessity=Necessity.MUST, weight=1.0)
        for s in p["position_skills"]
    ]
    return musts, [], None


def build_candidate(p: dict) -> CandidateProfile:
    """从黄金集行构造候选人画像（熟练度/年限来自弱监督注入字段）。"""
    if "candidate_skills" in p and "position_skills_must" in p:
        return CandidateProfile(
            user_id="eval",
            skills=[
                CandidateSkill(skill_id=s, skill_name=s, proficiency=p.get("candidate_proficiency", 2))
                for s in p["candidate_skills"]
            ],
            total_years=p.get("candidate_total_years", 5.0),
        )
    return CandidateProfile(
        user_id="eval",
        skills=[
            CandidateSkill(skill_id=s, skill_name=s, proficiency=2)
            for s in p["candidate_skills"]
        ],
        total_years=5.0,
    )


def evaluate_pairs(pairs: list[dict], weights, semantic, sim_threshold: float, jd_titles: dict[str, str] | None = None) -> dict:
    """对每对计算 total_score，与 label 计算 Spearman 与分类准确率。

    args:
        jd_titles: 可选。position_id → 该真实 JD 的原始标题（gold_title），
            仅用于让评测/报告展示真实 JD 名，而非归一化岗位名；
            不影响评分（评分只用黄金集行字面 must/nice）。

    返回 dict；Spearman 全相同分时为 nan，转为 0.0（避免 Optuna 崩溃）。
    """
    from scipy.stats import spearmanr

    from app.services.matching.engine import score_position

    scores, labels = [], []
    for p in pairs:
        musts, nices, req_years = build_position(p)
        # name 取真实 JD 标题（若可解析），否则回退 position_id——
        # 评测画像名落为**单条真实 JD**（name=JD 原始标题 / position_id），
        # 而非归一化岗位名（生产 loaders 用 p.name = 归一化求职岗位名）。
        name = (jd_titles or {}).get(p["position_id"]) or p["position_id"]
        position = PositionProfile(
            position_id=p["position_id"], name=name,
            must_skills=musts, nice_skills=nices,
            required_years=req_years,
        )
        candidate = build_candidate(p)
        r = score_position(
            candidate, position, weights=weights, semantic=semantic, sim_threshold=sim_threshold
        )
        scores.append(r.total_score)
        labels.append(p["label"])

    # 分类准确率：total_score ≥ 0.5 视为"匹配"
    hits = sum(1 for s, lb in zip(scores, labels) if (s >= 0.5) == (lb == 1))
    accuracy = hits / len(scores) if scores else 0.0
    corr = spearmanr(scores, labels).statistic
    return {
        "spearman": float(corr) if corr == corr else 0.0,
        "accuracy": accuracy,
        "scores": scores,
        "labels": labels,
    }


def _weights_tuple(w_must: float, w_nice: float, w_exp: float) -> tuple[float, float, float]:
    return (w_must, w_nice, w_exp)


def tune(pairs: list[dict], semantic, n_trials: int) -> dict:
    """Optuna 搜索权重 + sim_threshold，返回最优参数 dict。

    semantic 为 None 时退化为纯规则调优（sim_threshold 仍参与搜索但无效）。
    """
    import optuna

    def objective(trial) -> float:
        # 搜索空间保证 w_exp = 1 - w_must - w_nice ≥ 0（total_score 不允许为负）
        w_must = trial.suggest_float("w_must", 0.4, 0.7)
        w_nice = trial.suggest_float("w_nice", 0.1, 0.3)
        w_exp = 1.0 - w_must - w_nice
        threshold = trial.suggest_float("sim_threshold", 0.5, 0.95)
        return evaluate_pairs(pairs, _weights_tuple(w_must, w_nice, w_exp), semantic, threshold)["spearman"]

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    best = study.best_params
    # w_exp 取「已舍入 w_must/w_nice」的补数再舍入，保证写入值 Σw 严格 =1
    # （weights._valid_weights 第八轮起要求 Σw=1，三项独立舍入会引入 ≤0.001 偏差被拒）
    return {
        "w_must": round(best["w_must"], 3),
        "w_nice": round(best["w_nice"], 3),
        "w_exp": round(1.0 - round(best["w_must"], 3) - round(best["w_nice"], 3), 3),
        "sim_threshold": round(best["sim_threshold"], 3),
        "_spearman": study.best_value,
    }


def write_weights(params: dict) -> None:
    """最优参数写入 configs/match_weights.json（保留说明字段）。"""
    existing = {}
    if _WEIGHTS_PATH.exists():
        try:
            existing = json.loads(_WEIGHTS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    out = {
        "w_must": params["w_must"],
        "w_nice": params["w_nice"],
        "w_exp": params["w_exp"],
        "sim_threshold": params["sim_threshold"],
        "_comment": existing.get("_comment", "匹配权重（设计文档 9.3）。Optuna 搜索覆盖此文件。"),
    }
    _WEIGHTS_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="匹配权重 Optuna 调优")
    parser.add_argument("--trials", type=int, default=50)
    parser.add_argument("--no-semantic", action="store_true", help="纯规则调优（不注入 SBERT）")
    parser.add_argument("--eval-only", action="store_true", help="仅评估当前配置文件，不调优")
    parser.add_argument("--golden", type=Path, default=_GOLDEN_MATCH,
                        help="黄金集路径（默认 v1 golden_set_match.jsonl；"
                             "v2 传 golden_set_match_v2.jsonl，BT 补标注 08-16）")
    args = parser.parse_args()

    if not args.golden.exists():
        logger.warning("匹配黄金集不存在: %s", args.golden)
        return

    pairs = load_pairs(args.golden)
    semantic = None
    if not args.no_semantic and not args.eval_only:
        from app.services.matching.semantic import SkillEmbedder
        semantic = SkillEmbedder.get()
        logger.info("语义增强已启用（SBERT），首次加载模型需数秒...")

    if args.eval_only:
        from app.services.matching.weights import load_sim_threshold, load_weights

        weights = load_weights()
        threshold = load_sim_threshold()
        if not args.no_semantic:
            from app.services.matching.semantic import SkillEmbedder
            semantic = SkillEmbedder.get()
        result = evaluate_pairs(pairs, weights, semantic, threshold)
        logger.info("[评估] 当前权重 %s sim_threshold=%s", weights, threshold)
        logger.info("       Spearman=%.4f  Accuracy=%.4f", result["spearman"], result["accuracy"])
        return

    best = tune(pairs, semantic, n_trials=args.trials)
    spearman = best.pop("_spearman")
    write_weights(best)
    logger.info("[调优完成] Spearman=%.4f", spearman)
    logger.info("          权重写入 %s", _WEIGHTS_PATH.relative_to(_BACKEND_DIR))


if __name__ == "__main__":
    main()
