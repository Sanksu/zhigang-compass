"""回归测试：僵尸 JD 检测（_history_skill_sets 过滤语义）。

原实现 `if gs != skills` 排除与当前技能完全相同的历期（Jaccard=1.0 最强信号），
导致 detect_zombie_jd 的连续相似周期数永远不足 4 期，僵尸检测失效（H2）。
修复：仅排除当前 JD 自身（`r_id != jd_id`），完全相同技能的历期保留参与计数。
"""

from datetime import date

from app.services.data_quality.temporal_detector import (
    ZOMBIE_SAI_THRESHOLD,
    detect_zombie_jd,
)
from app.workers.tasks import _history_skill_sets


def test_history_skill_sets_keeps_identical_skill_periods():
    """与当前技能完全相同的历期必须保留（它们是僵尸判定的最强信号）。"""
    group = [
        (1, date(2026, 1, 1), ["Java", "Spring"]),
        (2, date(2026, 2, 1), ["Java", "Spring"]),   # 与当前完全相同
        (3, date(2026, 3, 1), ["Java", "Spring", "MySQL"]),  # 当前 JD 自身
    ]
    result = _history_skill_sets(group, jd_id=3)
    # 排除当前 JD(3)，保留历期 JD(1)/(2)，含完全相同技能的 JD(2)
    assert result == [{"Java", "Spring"}, {"Java", "Spring"}]


def test_history_skill_sets_excludes_only_current_jd():
    """仅排除当前 JD 自身，其余历期全部保留。"""
    group = [
        (10, date(2026, 1, 1), ["Go"]),
        (11, date(2026, 2, 1), ["Go"]),
        (12, date(2026, 3, 1), ["Go"]),   # 当前 JD
    ]
    assert _history_skill_sets(group, jd_id=12) == [{"Go"}, {"Go"}]


def test_zombie_detection_triggers_with_identical_history():
    """端到端：4 期技能几乎不变 + 高 SAI → detect_zombie_jd 应判 is_zombie=True。

    旧实现过滤掉完全相同的历期后 consecutive_periods 数不到 4 而失效。
    """
    identical = {"Java", "Spring", "Spring Boot"}
    history = [set(identical) for _ in range(4)]
    result = detect_zombie_jd(
        history,
        set(identical),
        sai=ZOMBIE_SAI_THRESHOLD + 0.1,  # SAI > 1.5
    )
    assert result.is_zombie is True
    assert result.consecutive_periods >= 4
