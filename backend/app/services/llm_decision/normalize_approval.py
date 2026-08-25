"""名称归一（position/skill normalize）审批通道的共享语义（PR3 c）。

供 admin approve 端点与 scripts/sync_dynamic_normalization.py 复用的纯函数集：
- 从 LLMDecisionRecord 解析 position/skill normalize 的（entity_type, action,
  source_name, target_name, primary_node_name）——approve 落 NameNormalizationRequest，
  sync 读取同一字段。
- 域 → 实体类型映射；可从 {proposal → approved} 的结构化输出推导审计意图。

红线：本模块不放 prompt/硬门（那在 position_name.py / skill_normalize.py，算法核心），
只收敛两域一致的结构解析与图变更参数的语义。
"""

from app.services.llm_decision import (
    DOMAIN_POSITION_NORMALIZE,
    DOMAIN_SKILL_NORMALIZE,
)

# 域 → 图谱实体类型（用于 NameNormalizationRequest.entity_type）
# 归并/改名 = 图节点变更 = R2 高风险动作，must 人工 approve，不 auto-apply。
_ENTITY_TYPE_BY_DOMAIN = {
    DOMAIN_POSITION_NORMALIZE: "position",
    DOMAIN_SKILL_NORMALIZE: "skill",
}


def entity_type_for_domain(domain: str) -> str:
    """决策域 → 图谱节点类型（position / skill）。非法域抛 ValueError。"""
    et = _ENTITY_TYPE_BY_DOMAIN.get(domain)
    if et is None:
        raise ValueError(f"非名称归一域 {domain!r}")
    return et


def parse_normalization(record) -> dict:
    """从决策记录解析归一化意图（approve 与 sync 共用）。

    returns {entity_type, action, source_name, target_name, primary_node_name}。
    规则（与决策器结构化输出对齐）：
    - skill_normalize：action = merge（仅 merge 需人工 apply；keep/noise 不写图）。
      source_name=entity_id（原始技能名），target_name=structured_output.target_standard。
      primary_node_name=target_standard（标准名保留）。
    - position_normalize：action = merge if is_new=False else rename。source_name=entity_id。
      target_name=structured_output.canonical_name（keep_original 时=原样，不写图）。
    """
    if record.domain == DOMAIN_SKILL_NORMALIZE:
        out = record.structured_output or {}
        target = str(out.get("target_standard") or "").strip()
        source = str(getattr(record, "entity_id", "") or "").strip()
        action = "merge" if (out.get("action") == "merge" and target) else ""
        return {
            "entity_type": entity_type_for_domain(record.domain),
            "action": action,
            "source_name": source,
            "target_name": target,
            "primary_node_name": target,
        }
    if record.domain == DOMAIN_POSITION_NORMALIZE:
        out = record.structured_output or {}
        target = str(out.get("canonical_name") or "").strip()
        source = str(getattr(record, "entity_id", "") or "").strip()
        # is_new=True → 图谱无该标准名，改名；否则并入候选标准名。
        action = "rename" if out.get("is_new") else "merge"
        if out.get("keep_original"):
            action = ""  # 原样确认，不产生图变更
        return {
            "entity_type": entity_type_for_domain(record.domain),
            "action": action,
            "source_name": source,
            "target_name": target,
            "primary_node_name": target,
        }
    raise ValueError(f"非名称归一域 {record.domain!r}")
