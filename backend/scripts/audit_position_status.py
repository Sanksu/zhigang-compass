"""岗位状态对账：审核列表（discovery_candidates）与图谱 Position.status 一致性审计。

背景（08-16 审查：审核列表 ↔ 图谱对应）：
- 风险 B：审核/自动流转为"Neo4j 先行、PG 后行"写序，PG commit 失败时图谱已改、
  列表未改且无补偿——周期性对账可发现漂移；--fix 按 PG 回写图谱
- 风险 C：候选池条目与图谱节点非一一对应——candidate 未入图属预期（趋势监测
  产物，图谱节点由 JD 聚合产生）；已审核岗位重建后图谱可能无节点（无 JD 支撑
  不建孤儿节点，见 rebuild_graph._restore_reviewed_statuses），对账按口径区分

对比口径：
- candidate：图谱允许无节点（未入图属预期，不告警）
- emerging/stable/declining/archived/rejected：图谱应存在节点且 status 一致；
  图谱无节点 → 报告"图谱缺失"；status 不一致 → 报告"状态漂移"
- active：图谱常态，不在候选池（PG 无记录，不参与对比）

用法（cwd=backend）：
    python -m scripts.audit_position_status             # 只读对账
    python -m scripts.audit_position_status --fix       # 按 PG 回写图谱（仅已存在节点）
"""

import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.database import async_session_factory, neo4j_driver
from app.core.logging import setup_logging
from app.models.business import DiscoveryCandidate

logger = setup_logging("audit_position_status")

# 参与对账的已审核状态（candidate 未入图属预期，不参与）
REVIEWED_STATES = ("emerging", "stable", "declining", "archived", "rejected")

_TZ_CN = timezone(timedelta(hours=8))
_REPORT_DIR = Path(__file__).resolve().parents[1] / "reports"


async def _load_candidates() -> dict[str, str]:
    """候选池 {position_name: state}。"""
    async with async_session_factory() as s:
        rows = (await s.scalars(select(DiscoveryCandidate))).all()
    return {r.position_name: r.state for r in rows}


def _load_graph_statuses() -> dict[str, str]:
    """图谱 {position_name: status}。"""
    with neo4j_driver.session() as session:
        rows = session.run(
            "MATCH (p:Position) RETURN p.name AS name, p.status AS status"
        ).data()
    return {r["name"]: r.get("status", "") for r in rows if r.get("name")}


def _reconcile(
    candidates: dict[str, str],
    graph: dict[str, str],
) -> dict:
    """对比候选池与图谱，返回按口径分类的对账结果。"""
    drift: list[dict] = []      # 已审核岗位：图谱存在但 status 不一致
    missing: list[dict] = []    # 已审核岗位：图谱无节点
    expected_missing: list[str] = []  # candidate 未入图（预期，仅统计）
    for name, state in candidates.items():
        if state == "candidate":
            if name not in graph:
                expected_missing.append(name)
            continue
        if name not in graph:
            missing.append({"position_name": name, "pg_state": state})
            continue
        graph_status = graph[name]
        if graph_status != state:
            drift.append({
                "position_name": name,
                "pg_state": state,
                "graph_status": graph_status,
            })
    return {
        "drift": drift,
        "missing": missing,
        "expected_missing_candidates": len(expected_missing),
    }


def _fix_drift(drift: list[dict]) -> int:
    """按 PG 状态回写图谱（仅已存在节点，MATCH 不建孤儿）。"""
    if not drift:
        return 0
    now = datetime.now(_TZ_CN).isoformat(timespec="seconds")
    with neo4j_driver.session() as session:
        for item in drift:
            session.run(
                """
                MATCH (p:Position {name: $name})
                SET p.status = $state, p.state_updated_at = $now
                """,
                name=item["position_name"],
                state=item["pg_state"],
                now=now,
            )
    return len(drift)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="岗位状态对账（审核列表 ↔ 图谱）")
    parser.add_argument("--fix", action="store_true", help="按 PG 回写图谱状态漂移项")
    args = parser.parse_args()

    candidates = asyncio.run(_load_candidates())
    graph = _load_graph_statuses()

    result = _reconcile(candidates, graph)
    pg_dist = Counter(candidates.values())
    graph_dist = Counter(
        s for s in graph.values() if s in (*REVIEWED_STATES, "candidate")
    )

    logger.info("=" * 56)
    logger.info("岗位状态对账（审核列表 ↔ 图谱）")
    logger.info("=" * 56)
    logger.info("候选池六态分布: %s", dict(pg_dist))
    logger.info("图谱已审核态分布: %s", dict(graph_dist))
    logger.info("candidate 未入图（预期）: %s 个", result["expected_missing_candidates"])
    logger.info("状态漂移: %s 个", len(result["drift"]))
    for item in result["drift"]:
        logger.warning(
            "  [漂移] %s: PG=%s 图谱=%s",
            item["position_name"], item["pg_state"], item["graph_status"],
        )
    logger.info("图谱缺失（已审核但无节点，无 JD 支撑）: %s 个", len(result["missing"]))
    for item in result["missing"]:
        logger.warning("  [缺失] %s: PG=%s", item["position_name"], item["pg_state"])

    fixed = 0
    if args.fix:
        fixed = _fix_drift(result["drift"])
        logger.info("已按 PG 回写图谱状态 %s 个岗位（仅已存在节点）", fixed)

    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = _REPORT_DIR / f"audit_position_status_{datetime.now(_TZ_CN):%Y%m%d}.json"
    path.write_text(
        json.dumps(
            {
                **result,
                "pg_distribution": dict(pg_dist),
                "graph_reviewed_distribution": dict(graph_dist),
                "fixed": fixed,
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("报告已写入: %s", path)

    # 退出码：漂移/缺失 > 0 时返回 1（CI/调度可据此告警）
    return 1 if (result["drift"] or result["missing"]) else 0


if __name__ == "__main__":
    sys.exit(main())
