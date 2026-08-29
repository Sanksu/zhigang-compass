"""管理后台全审批池只读汇总（endpoint `/admin/approvals/summary`，RBAC admin only）。

为「岗位审核」总览工作流面板提供跨审批流计数：候选晋升/演化晋级/衰退归档/
字典守卫/LLM 决策/技术观察池/技能别名。只读聚合，不触发任何状态流转、图写
副作用——各流计数口径与其独立审核端点一致（见各域路由注释）。

口径说明（与 openapi.yaml ApprovalStreamSummary / ApprovalSummaryData 对应）：
- pending   ：该流「待人工处理」数（候选池 candidate、emerging、declining、
              DictProposal.pending、LLMDecision status=proposal/shadow、
              TechnologyWatch.watch、SkillAlias.pending）
- review    ：需复核/被阻断数（候选低置信 final_confidence < 0.75 → 对齐前端
              REVIEW_BLOCK_THRESHOLD；dict-guard 无独立复核态置 0；LLM 记录
              status=blocked 归入阻断）
- approved  ：已通过/已生效数（晋升 emerging/stable/archived、提案 approved、
              LLM approved、观察池 candidate_promoted/archived、别名 approved）

assemble() 为纯函数（注入已算好的 streams），便于单测；构建各流计数的 SQL
在端点内按需执行。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.common import ok

router = APIRouter()

# 对齐前端 review-types.ts 的 REVIEW_BLOCK_THRESHOLD = 0.75 与后端
# services/discovery/confidence.py：低于 0.75 的候选标记「需复核」。此处用字面量
# 保持独立（不引入服务层依赖），更新须三方同步。
_REVIEW_CONFIDENCE = 0.75


def assemble(streams: list[dict]) -> dict:
    """汇总各审批流计数：计算三元汇总并按 pending 降序稳定排序（纯函数可测）。"""
    summary = {
        "total_pending": sum(s["pending"] for s in streams),
        "total_review": sum(s["review"] for s in streams),
        "total_approved": sum(s["approved"] for s in streams),
    }
    ordered = sorted(streams, key=lambda s: (-s["pending"], s["id"]))
    return {"summary": summary, "streams": ordered}


async def _count(db: AsyncSession, stmt) -> int:
    """执行 count 语句，NULL 归 0。"""
    return await db.scalar(stmt) or 0


@router.get("/approvals/summary")
async def approval_summary(db: AsyncSession = Depends(get_db)):
    """全审批池只读汇总（供总览工作流面板，仅计数不改状态）。"""
    from app.models.business import (
        DiscoveryCandidate,
        DictProposal,
        LLMDecisionRecord,
        SkillAlias,
        TechnologyWatch,
    )

    # ---- 岗位审核三流（discovery_candidates 六状态机） ----
    cand_pending = await _count(
        db, select(func.count()).select_from(DiscoveryCandidate).where(
            DiscoveryCandidate.state == "candidate"
        )
    )
    cand_review = await _count(
        db,
        select(func.count()).select_from(DiscoveryCandidate).where(
            DiscoveryCandidate.state == "candidate",
            DiscoveryCandidate.confidence["final_confidence"].as_float() < _REVIEW_CONFIDENCE,
        ),
    )
    cand_approved = await _count(
        db, select(func.count()).select_from(DiscoveryCandidate).where(
            DiscoveryCandidate.state.in_(("emerging", "stable", "archived"))
        )
    )

    evol_pending = await _count(
        db, select(func.count()).select_from(DiscoveryCandidate).where(
            DiscoveryCandidate.state == "emerging"
        )
    )
    evol_approved = await _count(
        db, select(func.count()).select_from(DiscoveryCandidate).where(
            DiscoveryCandidate.state == "stable"
        )
    )

    decl_pending = await _count(
        db, select(func.count()).select_from(DiscoveryCandidate).where(
            DiscoveryCandidate.state == "declining"
        )
    )
    decl_approved = await _count(
        db, select(func.count()).select_from(DiscoveryCandidate).where(
            DiscoveryCandidate.state == "archived"
        )
    )

    # ---- 字典守卫提案 ----
    dict_pending = await _count(
        db, select(func.count()).select_from(DictProposal).where(
            DictProposal.status == "pending"
        )
    )
    dict_approved = await _count(
        db, select(func.count()).select_from(DictProposal).where(
            DictProposal.status == "approved"
        )
    )

    # ---- LLM 决策（proposal/shadow 待审；blocked 归阻断） ----
    llm_pending = await _count(
        db, select(func.count()).select_from(LLMDecisionRecord).where(
            LLMDecisionRecord.status.in_(("proposal", "shadow"))
        )
    )
    llm_review = await _count(
        db, select(func.count()).select_from(LLMDecisionRecord).where(
            LLMDecisionRecord.status == "blocked"
        )
    )
    llm_approved = await _count(
        db, select(func.count()).select_from(LLMDecisionRecord).where(
            LLMDecisionRecord.status == "approved"
        )
    )

    # ---- 技术观察池 ----
    watch_pending = await _count(
        db, select(func.count()).select_from(TechnologyWatch).where(
            TechnologyWatch.status == "watch"
        )
    )
    watch_approved = await _count(
        db, select(func.count()).select_from(TechnologyWatch).where(
            TechnologyWatch.status.in_(("candidate_promoted", "archived"))
        )
    )

    # ---- 技能别名回写 ----
    alias_pending = await _count(
        db, select(func.count()).select_from(SkillAlias).where(
            SkillAlias.status == "pending"
        )
    )
    alias_approved = await _count(
        db, select(func.count()).select_from(SkillAlias).where(
            SkillAlias.status == "approved"
        )
    )

    streams = [
        {
            "id": "candidate_promotion",
            "label": "候选晋升",
            "route": "/admin/review?tab=candidate",
            "description": "candidate → emerging / rejected",
            "pending": cand_pending, "review": cand_review, "approved": cand_approved,
        },
        {
            "id": "evolution",
            "label": "演化晋级",
            "route": "/admin/review?tab=evolution",
            "description": "emerging → stable / declining",
            "pending": evol_pending, "review": 0, "approved": evol_approved,
        },
        {
            "id": "decline_archive",
            "label": "衰退归档",
            "route": "/admin/review?tab=evolution",
            "description": "declining → archived",
            "pending": decl_pending, "review": 0, "approved": decl_approved,
        },
        {
            "id": "dict_guard",
            "label": "字典守卫提案",
            "route": "/admin/review/dict",
            "description": "stopword/protect/清理提案",
            "pending": dict_pending, "review": 0, "approved": dict_approved,
        },
        {
            "id": "llm_decisions",
            "label": "LLM 决策回写",
            "route": "/admin/llm-decisions",
            "description": "propose → approved / rejected",
            "pending": llm_pending, "review": llm_review, "approved": llm_approved,
        },
        {
            "id": "tech_watch",
            "label": "技术观察池",
            "route": "/admin/review/watch",
            "description": "热点信号监测 / 提升候选",
            "pending": watch_pending, "review": 0, "approved": watch_approved,
        },
        {
            "id": "skill_aliases",
            "label": "技能别名回写",
            "route": "/admin/llm-decisions",
            "description": "别名 → 标准名审批",
            "pending": alias_pending, "review": 0, "approved": alias_approved,
        },
    ]

    return ok(data=assemble(streams))