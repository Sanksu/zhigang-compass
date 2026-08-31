"""LLM 决策统一信封——六域风险路由（主仓灰度底座）。

LLM 语义主导 + 确定性守卫的集中裁决点，供六域（JD 抽取/名称归一/分类/
簇命名/治理/技能关系）统一使用：

- R0：仅建议字段/标签/解释（suggest_*/label_*），验收后可灰度自动
- R1：低影响收紧动作（add_stopword 类），过硬门 + 置信度/影响面上限后
      可最多 10% 灰度自动（灰度比例由部署配置控制，本模块只出档位）
- R2：解禁/归并/删除/关系变更等高风险动作，一律进人工审核池
- blocked：不变量失败或证据不足，不写生产

默认 status=shadow（只落决策记录不生效）；proposal/auto 由各域按验收
窗口开启。正式决策经 build_record → persist_record 落 llm_decision_records
（不复制敏感原文，只用哈希/实体引用/证据摘要，见模型 docstring）。
"""

from typing import TYPE_CHECKING, Optional

from app.core import runtime_config

if TYPE_CHECKING:
    from app.models.business import LLMDecisionRecord

# ---- 六域 domain 常量（llm_decision_records.domain 枚举） ----
DOMAIN_JD_EXTRACT = "jd_extract"
DOMAIN_POSITION_NORMALIZE = "position_normalize"
DOMAIN_SKILL_NORMALIZE = "skill_normalize"
DOMAIN_POSITION_CLASSIFY = "position_classify"
DOMAIN_CLUSTER_LABEL = "cluster_label"
DOMAIN_CLUSTER_MEMBERSHIP = "cluster_membership"
DOMAIN_SKILL_CLASSIFY = "skill_classify"
DOMAIN_GOVERNANCE = "governance"
DOMAIN_SKILL_RELATION = "skill_relation"

DOMAINS: frozenset[str] = frozenset({
    DOMAIN_JD_EXTRACT,
    DOMAIN_POSITION_NORMALIZE,
    DOMAIN_SKILL_NORMALIZE,
    DOMAIN_POSITION_CLASSIFY,
    DOMAIN_CLUSTER_LABEL,
    DOMAIN_CLUSTER_MEMBERSHIP,
    DOMAIN_SKILL_CLASSIFY,
    DOMAIN_GOVERNANCE,
    DOMAIN_SKILL_RELATION,
})

# ---- 风险档位 ----
TIER_R0 = "R0"
TIER_R1 = "R1"
TIER_R2 = "R2"
TIER_BLOCKED = "blocked"

# ---- 状态机（status 取值） ----
STATUS_SHADOW = "shadow"
STATUS_PROPOSAL = "proposal"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_AUTO_APPLIED = "auto_applied"
STATUS_BLOCKED = "blocked"
STATUS_FAILED = "failed"
STATUS_REVERTED = "reverted"  # auto_applied 已被人工撤销（治理救济通道，副作用已反做）

# ---- 动作白名单 ----
# R0：只写建议字段/语义标签，可覆盖、无副作用，验收后可自动
R0_ACTIONS: frozenset[str] = frozenset({
    "suggest_category",
    "suggest_alias",
    "suggest_normalized_name",
    "label_cluster",
    "explain",
})
# R1：低影响收紧动作（字典过滤/隐藏类），过门禁后可灰度自动
R1_ACTIONS: frozenset[str] = frozenset({
    "add_stopword",
    "add_blocked",
    "hide_node",
})

# 默认门限（可由 runtime_config 覆盖：llm_decision_min_confidence /
# llm_decision_auto_impact_max，管理后台后续版本接入）
_DEFAULT_MIN_CONFIDENCE = 0.8
_DEFAULT_AUTO_IMPACT_MAX = 50


def validate_domain(domain: str) -> str:
    """校验 domain 在六域枚举内，非法抛 ValueError（防脏域值入表）。"""
    if domain not in DOMAINS:
        raise ValueError(
            f"未知决策域 {domain!r}（必须在 {sorted(DOMAINS)} 内）"
        )
    return domain


def risk_tier_for(
    domain: str,
    action: str,
    gate_ok: bool,
    confidence: Optional[float],
    impact_nodes: int = 0,
    min_confidence: Optional[float] = None,
    auto_impact_max: Optional[int] = None,
) -> tuple[str, str]:
    """统一风险路由：返回 (tier, reason)。

    - 不变量失败（gate_ok=False）→ blocked，任何动作不生效
    - R0_ACTIONS → R0（suggest/label 类）
    - R1_ACTIONS 且置信度达标且影响面 ≤ 上限 → R1
    - 其余（解禁/归并/删除/关系变更/证据不足）→ R2 进人工审核池

    min_confidence / auto_impact_max 缺省取 runtime_config（默认 0.8 / 50）。
    """
    validate_domain(domain)
    if not gate_ok:
        return TIER_BLOCKED, "不变量失败或证据不足，拒绝生效"
    if action in R0_ACTIONS:
        return TIER_R0, ""
    if action in R1_ACTIONS:
        if min_confidence is None:
            min_confidence = runtime_config.get("llm_decision_min_confidence", _DEFAULT_MIN_CONFIDENCE)
        if auto_impact_max is None:
            auto_impact_max = runtime_config.get("llm_decision_auto_impact_max", _DEFAULT_AUTO_IMPACT_MAX)
        effective_conf = confidence if confidence is not None else 0.0
        if effective_conf < min_confidence:
            return TIER_R2, f"置信度 {effective_conf:.2f} < 自动下限 {min_confidence}"
        if impact_nodes > auto_impact_max:
            return TIER_R2, f"影响面 {impact_nodes} > 自动上限 {auto_impact_max}"
        return TIER_R1, ""
    return TIER_R2, "高风险动作，需人工审核"


def build_record(
    *,
    domain: str,
    entity_type: str = "",
    entity_id: str = "",
    run_id: str = "",
    env: str = "production",
    input_hash: str = "",
    evidence_refs: Optional[list] = None,
    provider: str = "",
    model: str = "",
    prompt_version: str = "",
    schema_version: str = "",
    structured_output: Optional[dict] = None,
    postprocessed_output: Optional[dict] = None,
    confidence: Optional[float] = None,
    gate_result: str = "",
    risk_tier: str = "",
    status: str = STATUS_SHADOW,
    duration_ms: int = 0,
    attempts: int = 1,
    fallback_reason: str = "",
) -> "LLMDecisionRecord":
    """构造决策记录实例（不落库）；域非法/状态越界抛 ValueError。

    structured_output 之外的完整敏感原文不写入，只存哈希/引用/摘要。
    """
    from app.models.business import LLMDecisionRecord

    validate_domain(domain)
    if status not in {
        STATUS_SHADOW, STATUS_PROPOSAL, STATUS_APPROVED, STATUS_REJECTED,
        STATUS_AUTO_APPLIED, STATUS_BLOCKED, STATUS_FAILED, STATUS_REVERTED,
    }:
        raise ValueError(f"未知决策状态 {status!r}")
    return LLMDecisionRecord(
        domain=domain,
        entity_type=entity_type,
        entity_id=entity_id,
        run_id=run_id,
        env=env,
        input_hash=input_hash,
        evidence_refs=evidence_refs or [],
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        schema_version=schema_version,
        structured_output=structured_output or {},
        postprocessed_output=postprocessed_output,
        confidence=confidence,
        gate_result=gate_result,
        risk_tier=risk_tier,
        status=status,
        duration_ms=duration_ms,
        attempts=attempts,
        fallback_reason=fallback_reason[:200],
    )


async def persist_record(record: "LLMDecisionRecord") -> str:
    """落库一条决策记录，返回记录 id。

    独立短事务（自开 async session），调用方无需管理会话；写入失败抛
    异常由调用方决定是否阻塞业务（决策记录是审计事实源，默认应阻塞）。
    """
    from app.core.database import async_session_factory

    async with async_session_factory() as session:
        session.add(record)
        await session.commit()
        return record.id