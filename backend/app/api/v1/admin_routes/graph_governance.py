# -*- coding: utf-8 -*-
"""岗位域治理管理接口（2026-08-31 域治理 PR 链前端入口）。

- GET  /admin/graph-governance/summary：域划分总览 + 共成员基准得分 +
  cluster_membership 自审待审数（只读）
- POST /admin/graph-governance/resync：后台触发域重同步（骨干域+归类制，
  LLM 命名+成员自审；进程内单任务串行，进行中重复触发 409）

重同步为管理操作，只重算 domain_id/domain_name（幂等覆盖写），不动
岗位/技能/证据本体；治理口径与 scripts/sync_position_domains 同源。
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.database import get_db, neo4j_driver
from app.core.logging import setup_logging
from app.models.business import LLMDecisionRecord

logger = setup_logging("admin_graph_governance")

router = APIRouter(tags=["admin-graph-governance"])

GENERAL_DOMAIN_ID = "dom_general"
_TOP_MEMBERS = 12

# 进程内单任务串行标志（uvicorn 单实例部署口径；多副本部署需换分布式锁，当前口径不存在）
# 2026-09-04 批次B：删除 _resync_lock——running 标志在 add_task 前同步置位已足以防并发
# 触发；原锁成功路径从不 release，首次重同步后该功能永久 409（审查 P1）。
_resync_state = {"running": False, "last_resync": None}


def group_domains(rows: list[dict], top_members: int = _TOP_MEMBERS) -> list[dict]:
    """Neo4j 行（name/dom/dname/freq/source）→ 域列表（纯函数，供单测）。

    语义域按成员数降序，通用域恒排末位；域内成员按 freq 降序截断。
    每个成员携带 domain_source（归类依据分类），source_counts 为全成员
    来源分布（骨干/治理指派/归类/兜底/各类弃权原因）。
    """
    groups: dict[str, dict] = {}
    for r in rows:
        dom = r["dom"] or GENERAL_DOMAIN_ID
        groups.setdefault(dom, {"name": r.get("dname") or "", "members": []})
        groups[dom]["members"].append(
            {"name": r["name"], "freq": int(r.get("freq") or 0),
             "source": r.get("source"), "score": r.get("score")}
        )
    domains = []
    for dom_id, g in groups.items():
        members = sorted(
            g["members"], key=lambda m: (-m["freq"], m["name"]),
        )
        # 通用弃权域不截断：弃权原因需逐岗可见（可解释性口径）
        source_counts: dict[str, int] = {}
        for m in g["members"]:
            key = m["source"] or "unknown"
            source_counts[key] = source_counts.get(key, 0) + 1
        domains.append({
            "domain_id": dom_id,
            "domain_name": g["name"],
            "member_count": len(members),
            "members": members if dom_id == GENERAL_DOMAIN_ID else members[:top_members],
            "source_counts": dict(sorted(source_counts.items(), key=lambda kv: -kv[1])),
            "is_general": dom_id == GENERAL_DOMAIN_ID,
        })
    domains.sort(key=lambda d: (d["is_general"], -d["member_count"], d["domain_id"]))
    return domains


def assemble_summary(
    domains: list[dict],
    benchmark: dict | None,
    membership_pending: int,
    resync_running: bool,
    last_resync: str | None,
) -> dict:
    """总览装配（纯函数，供单测）。"""
    general = next((d for d in domains if d["is_general"]), None)
    return {
        "positions": sum(d["member_count"] for d in domains),
        "semantic_domains": sum(1 for d in domains if not d["is_general"]),
        "general_count": general["member_count"] if general else 0,
        "resync_running": resync_running,
        "last_resync": last_resync,
        "benchmark": benchmark,
        "domains": domains,
        "membership_pending": membership_pending,
    }


def _load_domain_rows() -> list[dict]:
    with neo4j_driver.session() as session:
        return session.run(
            """
            MATCH (p:Position) WHERE p.domain_id IS NOT NULL
            RETURN p.name AS name, p.domain_id AS dom,
                   p.domain_name AS dname, coalesce(p.freq, 0) AS freq,
                   p.domain_source AS source,
                   p.domain_score AS score
            """
        ).data()


def _load_benchmark() -> dict | None:
    """共成员基准评测（离线纯读；基准文件缺失/图谱不可达时返回 None）。"""
    try:
        from scripts.evaluate_position_domains import (
            _GOLDEN, _evaluate, _load_golden, _load_graph,
        )

        rows = _load_golden(_GOLDEN)
        stats, _results = _evaluate(rows, _load_graph())
        pairwise = stats.get("pairwise") or {}
        return {
            "evaluated": stats.get("evaluated") or 0,
            "strict_accuracy": stats.get("strict_accuracy"),
            "pairwise_f1": pairwise.get("f1"),
            "failures": stats.get("failures") or [],
        }
    except Exception as e:  # noqa: BLE001 — 基准不可用不阻塞总览
        logger.warning("[graph_governance] 基准评测不可用: %s", e)
        return None


@router.get("/graph-governance/summary")
async def graph_governance_summary(
    current_user: dict = Depends(require_permission("admin:*")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    membership_pending = await db.scalar(
        select(func.count()).select_from(LLMDecisionRecord).where(
            LLMDecisionRecord.domain == "cluster_membership",
            LLMDecisionRecord.status == "proposal",
        )
    )
    domains = group_domains(_load_domain_rows())
    benchmark = _load_benchmark()
    return {
        "code": 0,
        "msg": "ok",
        "data": assemble_summary(
            domains, benchmark,
            membership_pending=int(membership_pending or 0),
            resync_running=_resync_state["running"],
            last_resync=_resync_state["last_resync"],
        ),
    }


def _run_resync_task() -> None:
    """后台重同步主体：与 scripts 同口径（LLM 命名 + 自审），幂等覆盖写。"""
    try:
        from scripts.sync_position_domains import sync_position_domains

        stats = sync_position_domains(audit_membership=True)
        logger.info("[graph_governance] 重同步完成: %s", stats)
    except Exception as e:  # noqa: BLE001 — 失败只记日志，状态复位由 finally 保证
        logger.error("[graph_governance] 重同步失败: %s", e)
    finally:
        _resync_state["running"] = False
        _resync_state["last_resync"] = datetime.now(
            timezone(timedelta(hours=8)),
        ).isoformat()


@router.post("/graph-governance/resync")
async def graph_governance_resync(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_permission("admin:*")),
) -> dict:
    if _resync_state["running"]:
        raise HTTPException(status_code=409, detail="已有重同步任务进行中")
    try:
        _resync_state["running"] = True
        background_tasks.add_task(_run_resync_task)
    except Exception:
        _resync_state["running"] = False
        raise
    return {"code": 0, "msg": "ok", "data": {"started": True, "message": "重同步已受理，完成后总览自动更新"}}
