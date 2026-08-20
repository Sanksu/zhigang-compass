"""图谱版本与快照管理接口（设计文档 7.1 节版本管理）。

每次演化生成 `graph_v{date}.json` 全量快照，存入 PostgreSQL
`graph_versions.snapshot_json` JSONB 字段。版本快照保留 90 天，支持历史回溯对比。

M4 实现（原 M3 占位）：
- Neo4j 全量快照导出（{nodes, edges}，排除 Counter 等内部标签）
- 幂等 upsert（同日期版本覆盖更新，可安全重跑）
- 与上一版本 set 差集计算新增/删除/变化节点
- 90 天保留策略（删除更早版本）
"""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.core.database import async_session_factory, neo4j_driver
from app.models.business import GraphVersion
from app.services.evolution.schemas import GraphVersionMeta

# 图谱标签 → 快照节点 type（与前端 typeOf 的 pos_/sk_/ev_ 前缀推断一致）
_LABEL_TO_TYPE = {
    "Position": "position",
    "Skill": "skill",
    "Evidence": "evidence",
    "Course": "course",
    "Tool": "tool",
}

# 非业务实体标签（内部计数器等），不纳入快照
_SKIP_LABELS = {"Counter"}

# 版本保留天数（设计文档 7.1：快照保留 90 天）
VERSION_RETENTION_DAYS = 90

# 快照创建时区（T+1 版本号按 CST 日期）
_CST = timezone(timedelta(hours=8))

# 样本量对比告警阈值（机制补强 ①，PR #334 张恺天确认 D2：50%/200%）
_DATA_WARNING_LOWER_RATIO = 0.5
_DATA_WARNING_UPPER_RATIO = 2.0


def compute_data_warning(
    prev_nodes: list[dict],
    cur_nodes: list[dict],
    prev_edges: list[dict],
    cur_edges: list[dict],
) -> dict | None:
    """样本量对比告警（机制补强 ①，阈值 50%/200%）。

    任一证据量（Position 岗位数 / REQUIRES 边数）与上一版本相比萎缩 <50% 或
    膨胀 >200% → 返回告警对象；无上一版本（首个快照）或未越界 → None。
    目的：防"采集量波动被误判为能力变化"——Z-score 信号在证据量不足时失真，
    主动告警是"动态演化"的防御性设计。
    """
    prev_s = _evidence_stats(prev_nodes, prev_edges)
    cur_s = _evidence_stats(cur_nodes, cur_edges)
    if not any(prev_s.values()):
        return None
    issues: dict[str, dict] = {}
    for metric, prev_val in prev_s.items():
        cur_val = cur_s[metric]
        if prev_val <= 0:
            continue
        ratio = cur_val / prev_val
        if ratio < _DATA_WARNING_LOWER_RATIO or ratio > _DATA_WARNING_UPPER_RATIO:
            issues[metric] = {
                "prev": prev_val,
                "cur": cur_val,
                "ratio": round(ratio, 3),
                "direction": "shrunk" if ratio < _DATA_WARNING_LOWER_RATIO else "surged",
            }
    return issues or None


def _evidence_stats(nodes: list[dict], edges: list[dict]) -> dict:
    """快照证据量：Position 岗位数（jd 覆盖代理）+ REQUIRES 边数（岗位-技能关系）。"""
    return {
        "positions": sum(1 for n in nodes if n.get("type") == "position"),
        "requires_edges": sum(1 for e in edges if e.get("relation") == "REQUIRES"),
    }


class GraphVersionManager:
    """图谱版本管理器。

    - Neo4j 全量快照导出（APOC 简化：直接遍历节点/关系，不依赖 APOC 插件）
    - 幂等 upsert 到 PostgreSQL graph_versions
    - Diff 对比（set 差集计算新增/删除/变化节点）
    - 90 天保留策略
    - T+1 更新：每日 05:00 前发布新版本
    """

    async def create_snapshot(
        self,
        triggered_by: str = "scheduled",
        change_summary: str = "",
    ) -> GraphVersionMeta:
        """生成图谱快照并写入 PostgreSQL（幂等：同日期版本覆盖更新）。

        版本号按 CST 当日 `graph_v{YYYYMMDD}`；同日多次执行只保留最新快照，
        保证 T+1 语义（同一天内图谱数据不变，覆盖无副作用）。
        """
        # 同步 Neo4j 全量导出放线程池，避免阻塞事件循环（ARQ 心跳超时）
        nodes, edges = await asyncio.to_thread(self._export_neo4j)

        version_id = f"graph_v{datetime.now(_CST):%Y%m%d}"
        summary = change_summary or (
            f"全量快照：{len(nodes)} 节点 / {len(edges)} 边"
        )

        async with async_session_factory() as session:
            previous = await session.scalar(
                select(GraphVersion)
                .where(GraphVersion.id != version_id)
                .order_by(GraphVersion.created_at.desc())
            )
            added, removed, changed = self._diff_node_sets(previous, nodes)
            # 机制补强 ①：与上一版本对比证据量，萎缩/膨胀越界 → data_warning（防采集波动误判能力变化）
            prev_nodes = (previous.snapshot_json or {}).get("nodes", []) if previous else []
            prev_edges = (previous.snapshot_json or {}).get("edges", []) if previous else []
            data_warning = compute_data_warning(prev_nodes, nodes, prev_edges, edges)

            await session.execute(
                delete(GraphVersion).where(GraphVersion.id == version_id)
            )
            session.add(
                GraphVersion(
                    id=version_id,
                    change_summary=summary,
                    triggered_by=triggered_by,
                    snapshot_json={"nodes": nodes, "edges": edges},
                    node_added=added,
                    node_removed=removed,
                    node_changed=changed,
                    data_warning=data_warning,
                )
            )

            # 90 天保留：清理更早版本（基于 CST 当前时间）
            cutoff = datetime.now(_CST) - timedelta(days=VERSION_RETENTION_DAYS)
            await session.execute(
                delete(GraphVersion).where(GraphVersion.created_at < cutoff)
            )
            await session.commit()

        meta = GraphVersionMeta(
            version_id=version_id,
            created_at=datetime.now(_CST).isoformat(),
            change_summary=summary,
            triggered_by=triggered_by,
            node_added=added,
            node_removed=removed,
            node_changed=changed,
            data_warning=data_warning,
        )
        return meta

    @staticmethod
    def _export_neo4j() -> tuple[list[dict], list[dict]]:
        """导出 Neo4j 全量业务节点与关系（排除 Counter 等内部标签）。

        Returns:
            (nodes, edges)：nodes=[{id, name, type}]，edges=[{source, target}]
        """
        with neo4j_driver.session() as session:
            node_rows = session.run(
                """
                MATCH (n)
                WHERE NONE(l IN labels(n) WHERE l IN $skip)
                RETURN n.id AS id, n.name AS name, labels(n)[0] AS label
                """,
                skip=list(_SKIP_LABELS),
            ).data()
            edge_rows = session.run(
                """
                MATCH (a)-[r]->(b)
                WHERE NONE(l IN labels(a) WHERE l IN $skip)
                  AND NONE(l IN labels(b) WHERE l IN $skip)
                RETURN a.id AS source, b.id AS target, type(r) AS relation
                """,
                skip=list(_SKIP_LABELS),
            ).data()

        nodes = [
            {
                "id": row["id"],
                "name": row["name"] or row["id"],  # Evidence 等节点无 name 属性，退回 id
                "type": _LABEL_TO_TYPE.get(row["label"], row["label"]),
            }
            for row in node_rows
            if row["id"]
        ]
        edges = [
            {
                "source": row["source"],
                "target": row["target"],
                "relation": row["relation"],
            }
            for row in edge_rows
            if row["source"] and row["target"]
        ]
        return nodes, edges

    @staticmethod
    def _diff_node_sets(
        previous: GraphVersion | None,
        nodes: list[dict],
    ) -> tuple[int, int, int]:
        """与上一版本快照对比节点增减与变化（set 差集，设计文档 7.1 Diff）。

        Returns:
            (added, removed, changed)：added 新增节点数、removed 删除节点数、
            changed 两版本共有且 name/type 发生变化的节点数。
        """
        if previous is None:
            return len(nodes), 0, 0
        prev_nodes = {n["id"]: n for n in (previous.snapshot_json or {}).get("nodes", [])}
        cur_nodes = {n["id"]: n for n in nodes}
        prev_ids, cur_ids = set(prev_nodes), set(cur_nodes)
        added = len(cur_ids - prev_ids)
        removed = len(prev_ids - cur_ids)
        # node_changed：共有节点中 name/type 不同的数量（旧实现统计共有数，语义错误）
        changed = sum(
            1
            for nid in (cur_ids & prev_ids)
            if cur_nodes[nid].get("name") != prev_nodes[nid].get("name")
            or cur_nodes[nid].get("type") != prev_nodes[nid].get("type")
        )
        return added, removed, changed
