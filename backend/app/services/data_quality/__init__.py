"""数据质量检测模块。

对应设计文档 §4.7（数据时滞专项处理）与 §4.8（技能通胀专项处理）。
作为 ETL 管线中 `validate_temporal` 与 `detect_inflation` 两个步骤的实现。

- 阈值/降权系数严格对齐设计文档
- 输入参数以数据类形式声明，数据源由 ETL 层接入（validate_temporal 消费图谱
  first_seen_at + jd_raw 抽取结果，detect_inflation 消费 snapshot.extraction）
- 核心算法不依赖数据库 / Neo4j / Redis，便于单元测试
"""

from app.services.data_quality.inflation_detector import (
    InflationResult,
    classify_inflation,
    compute_inflation_score,
)
from app.services.data_quality.course_quality import (
    RECOMMEND_MIN_SCORE,
    evaluate_course,
    enrollment_score,
    platform_authority,
    project_density,
    rating_score,
    recency_score,
    skill_coverage,
)
from app.services.data_quality.schemas import (
    CourseQualityResult,
    JDSkillSet,
    PlagiarismResult,
    SAIResult,
    ZombieJDResult,
)
from app.services.data_quality.temporal_detector import (
    STALE_DECAY_WEIGHT,
    OBSOLETE_DECAY_WEIGHT,
    ZOMBIE_DECAY_WEIGHT,
    PLAGIARISM_DECAY_WEIGHT,
    apply_temporal_decay,
    compute_jaccard,
    compute_sai,
    detect_plagiarism,
    detect_zombie_jd,
)

__all__ = [
    # schemas
    "JDSkillSet",
    "SAIResult",
    "ZombieJDResult",
    "PlagiarismResult",
    "InflationResult",
    "CourseQualityResult",
    # temporal
    "compute_sai",
    "compute_jaccard",
    "detect_zombie_jd",
    "detect_plagiarism",
    "apply_temporal_decay",
    "STALE_DECAY_WEIGHT",
    "OBSOLETE_DECAY_WEIGHT",
    "ZOMBIE_DECAY_WEIGHT",
    "PLAGIARISM_DECAY_WEIGHT",
    # inflation
    "compute_inflation_score",
    "classify_inflation",
    # course quality
    "RECOMMEND_MIN_SCORE",
    "evaluate_course",
    "platform_authority",
    "rating_score",
    "enrollment_score",
    "recency_score",
    "skill_coverage",
    "project_density",
]
