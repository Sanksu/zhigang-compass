"""技能分类 LLM 审查（LLM 驱动化 P1：未分类技能灰度提议）。

定位：`Skill.category` 的权威事实源仍是 configs/skill_whitelist.yaml
（skill_category 枚举映射）。本模块只对「图谱中 category=未分类 且 低引用」
的技能做 LLM 分类提议——提议写入 `suggested_category*` 提议字段，
**不改动权威 category**；晋升（suggested→category）走人工确认后续通道。

与岗位名审查（position_review）同款灰度模式：
- 触发门：未分类 + 引用数 ≤ 阈值 + 尚无提议（同名不重复调 LLM）
- 枚举约束：分类必须 ∈ skill_whitelist.yaml 现行 23 类（schema 校验）
- 单条调用 15s 超时，LLM 失败静默跳过不阻塞管线
- 默认关闭（runtime_config.skill_category_review_enabled）

红线（AGENTS.md §4.1）：prompt 与触发门属算法核心，变更须算法岗张恺天 review。
"""

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.services.extraction.dictionary import SKILL_CATEGORY
from app.services.extraction.llm_invocation import invocation_scope

# 权威分类枚举（白名单 yaml 现行值，排除哨兵「未分类」）
KNOWN_CATEGORIES: frozenset[str] = frozenset(
    c for c in set(SKILL_CATEGORY.values()) if c and c != "未分类"
)

# 触发门：引用数上限（低风险候选，高频技能分类已由市场验证）
CLASSIFY_FREQ_MAX = 3
# 单条审查超时（s）
CLASSIFY_TIMEOUT_SECONDS = 15

SYSTEM_PROMPT = """你是招聘技能图谱的技能分类助手。给定一个技能名，从提供的
类别清单中选择唯一最合适的分类。只依据通用技术招聘市场常识判断，不臆造；
不确定时选择最接近的大类并给出较低置信度。"""

_TASK_TEMPLATE = """任务：为技能名 "{name}" 选择分类。

类别清单（必须从中选一，原样输出）：
{categories}

输出 JSON：
{{
  "category": "清单中的某一类",
  "confidence": 0.0到1.0,
  "reason": "一句话依据"
}}

要求：
1. category 必须与清单原文完全一致（含标点与斜杠），不得自创类别
2. 工具/框架按其所属技术领域归类（如 Docker → 云原生/DevOps）
3. 不确定时选大类并降低 confidence
"""


class SkillCategorySuggestion(BaseModel):
    """LLM 分类提议（Pydantic 强校验，幻觉防控第一道防线）。"""

    category: str = Field(description="分类名，必须来自现行权威枚举")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(default="")

    @model_validator(mode="after")
    def _check_known_category(self) -> "SkillCategorySuggestion":
        if self.category not in KNOWN_CATEGORIES:
            raise ValueError(f"未知分类: {self.category!r}（必须在现行枚举内）")
        return self


def should_classify(name: str, req_count: int, has_suggestion: bool) -> bool:
    """触发门：非空、低引用、尚无提议才分类。"""
    if not name or len(name.strip()) < 2 or has_suggestion:
        return False
    return (req_count or 0) <= CLASSIFY_FREQ_MAX


def classify_skill(
    name: str,
    llm,
    timeout: int = CLASSIFY_TIMEOUT_SECONDS,
) -> Optional[SkillCategorySuggestion]:
    """单条技能分类提议；LLM 未配置/失败返回 None（降级不写提议）。"""
    if llm is None or not name.strip():
        return None
    prompt = _TASK_TEMPLATE.format(
        name=name.strip(),
        categories="、".join(sorted(KNOWN_CATEGORIES)),
    )
    try:
        with invocation_scope("skill_category_review"):
            return llm.extract_structured(
                prompt,
                SkillCategorySuggestion,
                system_prompt=SYSTEM_PROMPT,
                timeout=timeout,
            )
    except Exception as e:
        from app.services.extraction.llm_provider import LLMExtractionError

        if not isinstance(e, LLMExtractionError):
            raise
        return None
