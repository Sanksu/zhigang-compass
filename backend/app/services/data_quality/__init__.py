"""数据质量检测模块。

对应设计文档 §4.7（数据时滞专项处理）与 §4.8（技能通胀专项处理）。
作为 ETL 管线中 `validate_temporal` 与 `detect_inflation` 两个步骤的实现。

M2 阶段交付框架骨架与纯函数算法：
- 阈值/降权系数严格对齐设计文档
- 输入参数以数据类形式声明，M3 LLM 抽取上线与图谱 first_seen_at 就位后仅需接入数据源
- 不依赖数据库 / Neo4j / Redis，便于单元测试
"""

from app.services.data_quality.inflation_detector import (
    InflationResult,
    classify_inflation,
    compute_inflation_score,
)
from app.services.data_quality.schemas import (
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
]
