"""技能关系 LLM 提议 worker（ETL 阶段 21，默认关）。

生成 proposal（llm_decision_records status=proposal，risk_tier=R2）进人工
审核池——admin 在决策页 approve 后由 scripts/sync_dynamic_relations.py 幂等
落图（skill_dynamic_relations 表）；不 auto-apply。

开关：runtime_config.skill_relation_propose_enabled（默认 False）；
候选上限：skill_relation_propose_max_candidates（默认 40）。
编排实现在 app/services/llm_decision/propose_relations.py（与 scripts 薄壳共用）。
"""

from app.core import runtime_config
from app.core.logging import setup_logging

logger = setup_logging("skill_relation_propose")


async def skill_relation_propose_daily(ctx: dict) -> dict:
    """技能关系提议（ARQ 注册名 skill_relation_propose_daily）。"""
    if not runtime_config.get("skill_relation_propose_enabled", False):
        return {"status": "skipped", "reason": "skill_relation_propose_enabled=false"}

    from app.services.llm_decision.propose_relations import propose

    limit = runtime_config.get("skill_relation_propose_max_candidates", 40)
    summary = await propose(limit=int(limit))
    logger.info("[skill_rel_propose] 完成: %s", summary.get("status"))
    return summary
