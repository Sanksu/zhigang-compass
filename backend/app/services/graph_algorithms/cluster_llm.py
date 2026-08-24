"""技能簇 LLM 兜底：JSON Schema 约束 + 触发条件 + 调用封装（图算法优化方案 §4.2-4.4）。

职责：
1. `ClusterLLMDecision`：LLM 输出的 JSON Schema（Pydantic，兼作 instructor 强校验）
2. `build_cluster_prompt`：LLM 输入（簇内技能按权重降序 + 明确任务）
3. `classify_cluster`：触发条件命中时调用 LLM（失败降级规则标签，不阻塞）

设计要点（对齐方案 §4.2-4.4）：
- 触发判断由 postprocess.ClusterPostProcessor 的 needs_llm 承担（规则优先后置），
  本模块只负责"已触发时"的调用与解析
- LLM 失败降级：捕获 LLMExtractionError/LLMConfigurationError → 返回
  ClusterLLMDecision(coherent=True, cluster_name=规则标签, splits=[])，与
  JD 抽取链路"LLM 失败规则兜底"同语义（llm_provider 模块 docstring）
- 按簇缓存由调用方（API 层）负责（Redis 24h TTL），本模块纯函数不持缓存
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.services.extraction.llm_provider import (
    LLMConfigurationError,
    LLMExtractionError,
    LLMTimeoutError,
    LLMProviderChain,
)
from app.services.extraction.llm_invocation import invocation_scope


class ClusterLLMDecision(BaseModel):
    """LLM 对技能簇的裁决（instructor JSON Schema 约束，§4.3 输出格式）。

    coherent=false 时 cluster_name 可为空、splits 描述应拆分的子技术栈。
    """
    coherent: bool = Field(description="该技能簇是否构成一个连贯的技术栈（true/false）")
    cluster_name: Optional[str] = Field(
        default=None, description="连贯时给出不超过 8 字的簇名称，如'大数据技术栈'"
    )
    rationale: Optional[str] = Field(
        default=None, description="判定理由（3 句以内）"
    )
    splits: list[str] = Field(
        default_factory=list,
        description="散乱时应拆分的子技术栈（最多 3 个），连贯时为空列表",
    )


# LLM 输入：技能名按共现权重降序排列，附触发原因（§4.3 输入格式）
_CLUSTER_PROMPT_TEMPLATE = """你是技术栈领域专家。以下是一个技能社区检测算法输出的技能簇，请：

1. 判断该簇是否为一个连贯的技术栈（coherent: true/false，附 3 句以内理由）
2. 若连贯，给出不超过 8 字的簇名称（如"大数据技术栈"、"前端工程化"）
3. 若散乱，列出应拆分的子技术栈（最多 3 个）

触发裁决原因（算法无法判定）：{triggers}
簇内技能（按共现权重降序）：
{skills}

严格按 JSON 输出，字段：coherent, cluster_name, rationale, splits。
"""


def build_cluster_prompt(
    skills: list[str],
    triggers: list[str],
) -> str:
    """构造 LLM 输入 prompt。

    Args:
        skills: 簇内技能名（调用方负责按共现权重降序传入）
        triggers: 触发原因（needs_llm 时的 triggers 列表，可空）
    """
    trigger_text = "、".join(triggers) if triggers else "无（规则标签为空）"
    skill_text = "\n".join(f"- {s}" for s in skills)
    return _CLUSTER_PROMPT_TEMPLATE.format(triggers=trigger_text, skills=skill_text)


class ClusterLLMClassifier:
    """技能簇 LLM 兜底裁决器。

    触发判断不在此处（由 postprocess 的 needs_llm 先行），本类只封装
    "已触发时"的 LLM 调用与失败降级。LLM 未配置/调用失败均降级规则标签。
    """

    def __init__(self, llm: Optional[LLMProviderChain] = None):
        try:
            self._llm = llm or LLMProviderChain()
        except LLMConfigurationError:
            self._llm = None

    def classify(
        self,
        skills: list[str],
        triggers: list[str],
        rule_label: str = "",
    ) -> ClusterLLMDecision:
        """裁决一个技能簇；LLM 不可用/失败时降级规则标签。

        Args:
            skills: 簇内技能名（按权重降序）
            triggers: 触发原因
            rule_label: 规则标签（降级兜底）
        """
        if not skills:
            return ClusterLLMDecision(coherent=True, cluster_name=rule_label or None)
        if self._llm is None:
            # 未配置 provider：降级规则标签，与 JD 抽取"无 key 规则兜底"一致
            return ClusterLLMDecision(coherent=True, cluster_name=rule_label or None)

        prompt = build_cluster_prompt(skills, triggers)
        try:
            # 同步路由契约（设计文档 §6.5，G-04）：单 provider 10s 超时单次尝试，
            # 不重试不切换（技能簇接口在 API 请求路径上，避免同步阻塞）
            with invocation_scope("cluster_label"):
                return self._llm.call_sync(prompt, ClusterLLMDecision)
        except (LLMExtractionError, LLMTimeoutError, LLMConfigurationError):
            # LLM 调用失败（超时/熔断/校验/未配置）：降级规则标签，不阻塞 API
            return ClusterLLMDecision(coherent=True, cluster_name=rule_label or None)
