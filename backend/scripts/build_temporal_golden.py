"""时滞检测合成序列标注集生成（DA-M3-04，设计文档 §4.7）。

三类时滞（SAI 内容时滞 / 僵尸 JD / 抄袭时滞）均为时间序列判断，单条标注
不充分，故按检测器输入构造序列样本。真值由场景意图判定（gold_* 字段），
不依赖检测器输出。

场景覆盖：
- SAI：jd 技能偏老 vs 岗位近期技能新 → stale/obsolete；反之为 fresh
- 僵尸 JD：连续 ≥4 周期技能集合 Jaccard≥0.95 + SAI 高 → zombie
- 抄袭：新 JD 技能为旧 JD 子集 + 间隔 >90 天 → plagiarism

用法：
    uv run python scripts/build_temporal_golden.py
    uv run python scripts/build_temporal_golden.py --dry-run
"""

import argparse
import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("build_temporal_golden")

_OUTPUT = _BACKEND_DIR / "data" / "golden_set" / "golden_set_temporal.jsonl"

# ── SAI 场景：(id, jd_skill_ages, position_recent_skill_ages, gold_sai_label) ──
# gold_sai_label ∈ fresh / content_stale / content_obsolete
_SAI_SCENARIOS: list[tuple] = [
    ("tmp_001", [180, 220, 250], [80, 100, 120], "content_obsolete", "JD 技能普遍老（220 天），岗位近期 JD 技能新（100 天），SAI≈2.2"),
    ("tmp_002", [160, 200], [90, 110], "content_stale", "JD 技能中位 180 天 vs 近期 100 天，SAI=1.8"),
    ("tmp_003", [170, 210], [60, 90, 120], "content_obsolete", "JD 技能明显偏老，SAI≈2.1"),
    ("tmp_004", [120, 150, 180], [70, 80, 100], "content_stale", "JD 技能中位 150 vs 近期 80，SAI=1.875"),
    ("tmp_005", [40, 60, 80], [40, 60, 80, 100], "fresh", "JD 技能新，与近期分布一致"),
    ("tmp_006", [30, 50], [90, 100], "fresh", "JD 技能比岗位历史更年轻"),
    ("tmp_007", [140, 160], [100, 100], "fresh", "SAI=1.5 恰在阈值上（>1.5 才 stale），边界负样本"),
    ("tmp_008", [60, 90], [60, 90], "fresh", "JD 技能与岗位历史一致，SAI=1.0"),
]

# ── 僵尸 JD 场景：(id, history_skills, current_skills, sai, gold_is_zombie) ──
_ZOMBIE_SCENARIOS: list[tuple] = [
    ("tmp_009", [["Java", "Spring", "MySQL"]] * 4, ["Java", "Spring", "MySQL"], 2.0, True,
     "连续 4 周期技能完全相同 + SAI 高 → 僵尸 JD"),
    ("tmp_010", [["Python", "Flask"]] * 4, ["Python", "Flask"], 1.8, True,
     "连续 4 周期技能完全相同 + SAI 偏高 → 僵尸 JD"),
    ("tmp_011", [["Java", "Spring"]] * 4, ["Java", "Spring"], 1.0, False,
     "技能 4 周期不变但 SAI=1.0 不偏老 → 非僵尸"),
    ("tmp_012", [["Java", "Spring"], ["Java", "Spring", "MyBatis"], ["Java", "Spring", "Redis"], ["Java", "Spring", "Kafka"]],
     ["Java", "Spring", "Kafka", "Docker"], 1.2, False, "技能集合持续演化，Jaccard<0.95 → 非僵尸"),
    ("tmp_013", [["Java", "Spring"], ["Java", "Spring"]], ["Java", "Spring"], 2.0, False,
     "仅 2 个历史周期，连续相似数不足 4 → 非僵尸"),
]

# ── 抄袭场景：(id, old_skills, old_days_ago, new_skills, gold_is_plagiarism) ──
_PLAGIARISM_SCENARIOS: list[tuple] = [
    ("tmp_014", ["A", "B", "C", "D"], 200, ["A", "B", "C"], True,
     "新 JD 技能为旧 JD 子集 + 间隔 200 天 → 抄袭改日期"),
    ("tmp_015", ["Python", "Django", "DRF", "PostgreSQL"], 150, ["Python", "Django", "PostgreSQL"], True,
     "子集关系 + 间隔 150 天 → 抄袭"),
    ("tmp_016", ["A", "B", "C"], 200, ["A", "B", "D"], False,
     "新 JD 含旧 JD 没有的技能 D → 非子集 → 非抄袭"),
    ("tmp_017", ["A", "B", "C", "D"], 60, ["A", "B", "C"], False,
     "子集但间隔仅 60 天（≤90）→ 非抄袭"),
    ("tmp_018", ["A", "B", "C"], 30, ["A", "B", "C"], False,
     "技能相同且间隔 30 天（新近发布）→ 非抄袭"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="时滞检测合成序列标注集生成")
    parser.add_argument("--dry-run", action="store_true", help="仅打印统计不落盘")
    args = parser.parse_args()

    records: list[dict] = []

    for sid, jd_ages, recent_ages, label, scenario in _SAI_SCENARIOS:
        records.append({
            "id": sid, "kind": "sai", "scenario": scenario,
            "jd_skill_ages": jd_ages,
            "position_recent_skill_ages": recent_ages,
            "gold_sai_label": label,
            "gold_abnormal": label != "fresh",
        })
    for sid, history, current, sai, gold, scenario in _ZOMBIE_SCENARIOS:
        records.append({
            "id": sid, "kind": "zombie", "scenario": scenario,
            "history_skills": history, "current_skills": current, "sai": sai,
            "gold_is_zombie": gold,
            "gold_abnormal": gold,
        })
    for sid, old_skills, days, new_skills, gold, scenario in _PLAGIARISM_SCENARIOS:
        records.append({
            "id": sid, "kind": "plagiarism", "scenario": scenario,
            "old_skills": old_skills, "old_days_ago": days, "new_skills": new_skills,
            "gold_is_plagiarism": gold,
            "gold_abnormal": gold,
        })

    pos = sum(1 for r in records if r["gold_abnormal"])
    neg = len(records) - pos
    kinds = {}
    for r in records:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    logger.info(f"时滞标注集: 共 {len(records)} 条（正样本 {pos} / 负样本 {neg}）按类型 {kinds}")

    if args.dry_run:
        return
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    logger.info(f"已写入 {_OUTPUT.relative_to(_BACKEND_DIR)}")


if __name__ == "__main__":
    main()
