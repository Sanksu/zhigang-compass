"""数据时滞专项检测（设计文档 §4.7）。

三类时滞检测均为纯函数：
- 内容时滞：SAI（技能老化指数）
- 僵尸 JD：连续 N 周期技能集合几乎不变 + SAI 偏老
- 抄袭时滞：技能子集关系 + 跨 90 天时间窗口

降权系数严格对齐设计文档：
- content_stale ×0.5 / content_obsolete ×0（归档不入聚合）
- zombie_jd ×0.3 / plagiarism ×0.4
"""

from statistics import median

from app.services.data_quality.schemas import (
    JDSkillSet,
    PlagiarismResult,
    SAIResult,
    ZombieJDResult,
)

# ── 设计文档 §4.7.2 阈值 ──
SAI_STALE_THRESHOLD = 1.5      # > 1.5 标记 content_stale
SAI_OBSOLETE_THRESHOLD = 2.0   # > 2.0 标记 content_obsolete
RECENT_WINDOW_DAYS = 90        # 同岗位近期 JD 窗口

# ── 设计文档 §4.7.2/4.7.3 降权系数 ──
FRESH_DECAY_WEIGHT = 1.0
STALE_DECAY_WEIGHT = 0.5
OBSOLETE_DECAY_WEIGHT = 0.0    # obsolete 归档不入聚合
ZOMBIE_DECAY_WEIGHT = 0.3
PLAGIARISM_DECAY_WEIGHT = 0.4

# ── 设计文档 §4.7.2 僵尸 JD 阈值 ──
ZOMBIE_JACCARD_THRESHOLD = 0.95
ZOMBIE_SAI_THRESHOLD = 1.5
ZOMBIE_CONSECUTIVE_PERIODS = 4   # 约 4 个月

# ── 设计文档 §4.7.3 抄袭时滞阈值 ──
PLAGIARISM_DAYS_THRESHOLD = 90


def compute_jaccard(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard 相似度：|A∩B| / |A∪B|。

    两集合均为空时返回 0.0（避免 ZeroDivisionError，且语义上无相似可言）。
    """
    if not set_a and not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def compute_sai(
    jd_skill_ages: list[int],
    position_recent_skill_ages: list[int],
) -> float:
    """计算技能老化指数 SAI（设计文档 §4.7.2）。

    SAI = median(skill_age of jd) / median(skill_age of 岗位近 90 天 JD)

    参数：
        jd_skill_ages: 待评估 JD 的各技能首见时长（天）
        position_recent_skill_ages: 同岗位近 90 天所有 JD 的技能首见时长聚合

    返回：
        SAI 值。任一侧为空返回 0.0（数据不足时不武断判定为时滞）。
    """
    if not jd_skill_ages or not position_recent_skill_ages:
        return 0.0
    jd_median = median(jd_skill_ages)
    position_median = median(position_recent_skill_ages)
    if position_median == 0:
        # 岗位近 90 天参考技能中位首见时长为 0（全是新技能）：JD 中位数 > 0
        # 说明其技能相对岗位整体明显更旧 → 视为过时；两侧均 0 才返回 0（数据不足）。
        # 返回越过 obsolete 阈值的有限上界，避免 JSON 序列化 inf。
        if jd_median > 0:
            return float(SAI_OBSOLETE_THRESHOLD + 1.0)
        return 0.0
    return float(jd_median / position_median)


def classify_sai(
    sai: float,
    stale_threshold: float | None = None,
    obsolete_threshold: float | None = None,
) -> SAIResult:
    """按 SAI 阈值划分时滞等级并给出降权系数。

    stale_threshold / obsolete_threshold 可覆盖（DA-M3-04 调优），
    缺省（None）取运行时配置 configs/data_quality_thresholds.json，
    默认值对应设计文档 §4.7.2 固定值 1.5 / 2.0。
    """
    if stale_threshold is None:
        from app.services.data_quality.thresholds import load_sai_stale_threshold

        stale_threshold = load_sai_stale_threshold()
    if obsolete_threshold is None:
        from app.services.data_quality.thresholds import load_sai_obsolete_threshold

        obsolete_threshold = load_sai_obsolete_threshold()
    if sai > obsolete_threshold:
        return SAIResult(sai=sai, label="content_obsolete", decay_weight=OBSOLETE_DECAY_WEIGHT)
    if sai > stale_threshold:
        return SAIResult(sai=sai, label="content_stale", decay_weight=STALE_DECAY_WEIGHT)
    return SAIResult(sai=sai, label="fresh", decay_weight=FRESH_DECAY_WEIGHT)


def detect_zombie_jd(
    history_jd_skills: list[set[str]],
    current_jd_skills: set[str],
    sai: float,
    consecutive_periods: int | None = None,
    jaccard_threshold: float | None = None,
    min_periods: int | None = None,
    sai_threshold: float | None = None,
) -> ZombieJDResult:
    """僵尸 JD 检测（设计文档 §4.7.2）。

    判定条件（同时满足）：
    - 连续 N 个发布周期（默认 4）技能集合 Jaccard ≥ 0.95
    - SAI > 1.5（技能偏老）

    参数：
        history_jd_skills: 历史周期 JD 的技能集合，按发布时间升序
        current_jd_skills: 当前 JD 技能集合
        sai: 当前 JD 的 SAI 值（外部预算后传入，避免重复计算）
        consecutive_periods: 显式传入连续周期数；为 None 时按历史序列尾部连续相似计数
        jaccard_threshold / min_periods / sai_threshold: 缺省（None）取运行时配置
            configs/data_quality_thresholds.json（DA-M3-04 调优可显式覆盖）

    处置：仅保留最早版本，后续版本降权 ×0.3。
    """
    if jaccard_threshold is None:
        from app.services.data_quality.thresholds import load_zombie_jaccard_threshold

        jaccard_threshold = load_zombie_jaccard_threshold()
    if min_periods is None:
        from app.services.data_quality.thresholds import load_zombie_consecutive_periods

        min_periods = load_zombie_consecutive_periods()
    if sai_threshold is None:
        from app.services.data_quality.thresholds import load_zombie_sai_threshold

        sai_threshold = load_zombie_sai_threshold()
    if consecutive_periods is None:
        consecutive_periods = _count_consecutive_similar(
            history_jd_skills, current_jd_skills, jaccard_threshold
        )

    if not history_jd_skills:
        last_jaccard = 0.0
    else:
        last_jaccard = compute_jaccard(history_jd_skills[-1], current_jd_skills)

    is_zombie = (
        consecutive_periods >= min_periods
        and sai > sai_threshold
    )
    return ZombieJDResult(
        is_zombie=is_zombie,
        jaccard=last_jaccard,
        consecutive_periods=consecutive_periods,
        decay_weight=ZOMBIE_DECAY_WEIGHT if is_zombie else FRESH_DECAY_WEIGHT,
    )


def _count_consecutive_similar(
    history: list[set[str]],
    current: set[str],
    threshold: float,
) -> int:
    """从历史序列尾部向前数连续相似周期数（包含 current 自身）。"""
    count = 1  # 当前 JD 自身算第 1 个周期
    for prev in reversed(history):
        if compute_jaccard(prev, current) >= threshold:
            count += 1
        else:
            break
    return count


def detect_plagiarism(
    new_jd: JDSkillSet,
    old_jd: JDSkillSet,
    days_threshold: int | None = None,
) -> PlagiarismResult:
    """抄袭时滞检测（设计文档 §4.7.3）。

    判定条件（同时满足）：
    - 新 JD 技能集合是旧 JD 的子集（抄袭后删除部分要求）
    - 发布时间间隔 > 90 天

    days_threshold 缺省（None）取运行时配置 configs/data_quality_thresholds.json。
    处置：降权 ×0.4 不参与聚合。
    """
    if days_threshold is None:
        from app.services.data_quality.thresholds import load_plagiarism_days

        days_threshold = load_plagiarism_days()
    new_skills = set(new_jd.skills)
    old_skills = set(old_jd.skills)
    is_subset = new_skills.issubset(old_skills) and bool(new_skills)
    days_interval = (new_jd.publish_date - old_jd.publish_date).days
    is_plagiarism = is_subset and days_interval > days_threshold
    return PlagiarismResult(
        is_plagiarism=is_plagiarism,
        is_subset=is_subset,
        days_interval=days_interval,
        decay_weight=PLAGIARISM_DECAY_WEIGHT if is_plagiarism else FRESH_DECAY_WEIGHT,
    )


def apply_temporal_decay(
    base_weight: float,
    sai_result: SAIResult | None = None,
    zombie_result: ZombieJDResult | None = None,
    plagiarism_result: PlagiarismResult | None = None,
) -> float:
    """叠加时滞降权：取最严重者。

    设计文档中三类时滞降权独立标记，但实际入聚合时只取一个 effective weight，
    避免多次降权后权重过小（如 stale × zombie = 0.15）。
    """
    weights = [base_weight]
    if sai_result is not None:
        weights.append(sai_result.decay_weight)
    if zombie_result is not None:
        weights.append(zombie_result.decay_weight)
    if plagiarism_result is not None:
        weights.append(plagiarism_result.decay_weight)
    return min(weights)


