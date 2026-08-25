"""名称归一 LLM 提议 worker（ETL 阶段 20，默认关）。

与阶段 19 shadow 的区别：本 worker 生成 proposal（llm_decision_records
status=proposal，risk_tier=R2），进入人工审核池——admin 在决策页 approve 后
由 scripts/sync_dynamic_normalization.py 幂等落图；不 auto-apply。

开关：runtime_config.name_normalization_propose_enabled（默认 False）；
候选上限：name_normalization_propose_max_candidates（默认 40）。
编排实现在 app/services/llm_decision/propose_normalization.py（与 scripts 薄壳共用）。
"""

from app.core import runtime_config
from app.core.logging import setup_logging

logger = setup_logging("name_normalization_propose")


async def name_normalization_propose_daily(ctx: dict) -> dict:
    """名称归一提议（ARQ 注册名 name_normalization_propose_daily）。"""
    if not runtime_config.get("name_normalization_propose_enabled", False):
        return {"status": "skipped", "reason": "name_normalization_propose_enabled=false"}

    from app.services.llm_decision.propose_normalization import propose

    limit = runtime_config.get("name_normalization_propose_max_candidates", 40)
    summary = await propose(limit=int(limit), domain="all")
    logger.info("[name_norm_propose] 完成: %s", summary.get("status"))
    return summary
