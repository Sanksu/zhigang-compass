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

类别锚点示例（近邻类目易混，按本表口径判）：
{anchors}

边界口径（权威分类政策，易混对按此裁决）：
{boundary_rules}

输出 JSON：
{{
  "category": "清单中的某一类",
  "confidence": 0.0到1.0,
  "reason": "一句话依据"
}}

要求：
1. category 必须与清单原文完全一致（含标点与斜杠），不得自创类别
2. 工具/框架按其所属技术领域归类（如 Docker → 云原生/DevOps）
3. 查询/脚本语言（SQL、Shell 等）归编程语言；具体数据库产品（MySQL、
   Redis 等）归数据库；语言 vs 其上框架分属编程语言 vs 对应领域
4. 不确定时选大类并降低 confidence
"""

# 边界口径规则（校准 r5，2026-08-25）：四轮复测（r1-r4）稳定的 16 类错误
# 全为语义边界政策题（词面近邻证据 12/16 命中为 0，无法用检索证据解），
# 本块把白名单分类政策显式化——等价于给标注员的口径手册。
# 错误来源实证：docs/reviews/LLM驱动黄金集复测r4_20260824.md。
# 红线：口径规则属算法核心，变更须张恺天 review。
_BOUNDARY_RULES = """\
- 数据治理/统计/仓库类（数据质量、数据统计、数据仓库）→ 大数据；商业分析与报表 → 数据分析/商业
- RPC/微服务框架（Dubbo、gRPC）→ 后端；消息系统（Kafka、RabbitMQ）→ 消息/中间件
- 分布式存储/存储引擎（Ceph、HBase）→ 数据库；容器与编排（K8s）→ 云原生/DevOps
- 办公套件/项目管理/构建工具/系统集成（Microsoft 365、CMake、Maven、Jira）→ 工程协作
- 车载感知/传感器（毫米波雷达、激光雷达）→ 智能驾驶/机器人；EDA/芯片设计工具（Allegro、Vivado）→ 硬件/芯片
- 学科课程类（计算机网络、操作系统）→ 计算机基础；具体网络协议（TCP/IP、HTTP）→ 网络/协议；流媒体协议（RTMP、WebRTC、HLS）→ 音视频
- 语言标准/版本（ES5、ES6）与编程范式概念（异步编程、函数式编程）→ 编程语言；JS 运行时与工具链（Node.js、Bun、npm）→ 前端
- 数学基础（线性代数、概率论、统计推断）→ 数据分析/商业"""


def category_anchors(max_per_category: int = 2) -> str:
    """类别锚点示例（从权威白名单取每类代表词，近邻类目易混口径）。

    基线实证：分类 top-1 0.8714 的错误集中在同域近邻（SQL→数据库、
    ES5→前端、异步编程→计算机基础）——锚点把权威口径的边界词显式给到
    prompt（校准 r1，算法红线：口径变更须算法岗确认）。
    """
    from app.services.extraction.dictionary import SKILL_CATEGORY

    by_category: dict[str, list[str]] = {}
    for skill, category in SKILL_CATEGORY.items():
        by_category.setdefault(category, []).append(skill)
    lines = []
    for category in sorted(by_category):
        reps = sorted(by_category[category], key=lambda s: (len(s), s))[:max_per_category]
        if reps:
            lines.append(f"- {category}：{'、'.join(reps)}")
    return "\n".join(lines)


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
        anchors=category_anchors(),
        boundary_rules=_BOUNDARY_RULES,
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
