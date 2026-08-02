"""弱监督匹配黄金集构造（AL-M3-03）。

设计文档 9.3 要求 100 对匹配黄金集支撑 Spearman 评估与 Optuna 调优，
当前仅有 JD 黄金集。本脚本从 `jd_golden_100.jsonl` 的 gold_skills 派生人岗匹配对：

- 正样本：候选技能 = 岗位技能随机子集（保留 ≥60%，即"候选人具备该岗多数技能"）
- 负样本：候选技能 = 他岗技能子集（与目标岗位技能重叠 ≤1，即"明显不匹配"）

每岗位 1 正 + 2 负。固定 seed（可复现、可审计）。

输出：`data/golden_set/golden_set_match.jsonl`
"""

import json
import random
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_GOLDEN_JD = _BACKEND_DIR / "data" / "golden_set" / "jd_golden_100.jsonl"
_OUTPUT = _BACKEND_DIR / "data" / "golden_set" / "golden_set_match.jsonl"

NEGATIVES_PER_POSITION = 2
SEED = 42


def _skills(item: dict) -> list[str]:
    """gold_skills 字段技能集（去空）。"""
    return [s for s in item.get("gold_skills") or [] if s.strip()]


def main() -> None:
    if not _GOLDEN_JD.exists():
        print(f"[SKIP] JD 黄金集不存在: {_GOLDEN_JD}")
        return

    items = [
        json.loads(line)
        for line in _GOLDEN_JD.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    records = [(it["id"], _skills(it)) for it in items if _skills(it)]
    if not records:
        print("[SKIP] 无有效技能记录的 JD")
        return

    rng = random.Random(SEED)
    pairs: list[dict] = []
    for pos_id, skills in records:
        # 正样本：候选人具备该岗 ≥60% 技能
        keep = max(1, int(len(skills) * 0.6))
        pairs.append({
            "candidate_skills": rng.sample(skills, keep),
            "position_skills": skills,
            "label": 1,
            "position_id": pos_id,
        })
        # 负样本：他岗技能子集（与目标岗位技能重叠 ≤1）
        others = [
            s for pid, s in records
            if pid != pos_id and len(set(skills) & set(s)) <= 1
        ]
        rng.shuffle(others)
        for other in others[:NEGATIVES_PER_POSITION]:
            cand = rng.sample(other, max(1, int(len(other) * 0.7)))
            pairs.append({
                "candidate_skills": cand,
                "position_skills": skills,
                "label": 0,
                "position_id": pos_id,
            })

    with _OUTPUT.open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    pos = sum(1 for p in pairs if p["label"] == 1)
    neg = len(pairs) - pos
    print(f"生成 {len(pairs)} 对（正 {pos} / 负 {neg}）→ {_OUTPUT.name}")


if __name__ == "__main__":
    main()
