"""图谱版本与快照管理接口（设计文档 7.1 节版本管理）。

每次演化生成 `graph_v{date}.json` 全量快照，存入 PostgreSQL
`graph_versions.snapshot_json` JSONB 字段。版本快照保留 90 天，支持历史回溯对比。

M4 实现（原 M3 占位）：
- Neo4j 全量快照导出（{nodes, edges}，排除 Counter 等内部标签）
- 幂等 upsert（同日期版本覆盖更新，可安全重跑）
- 与上一版本 set 差集计算新增/删除/变化节点
- 90 天保留策略（删除更早版本）
"""

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
        nodes, edges = self._export_neo4j()

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
                RETURN a.id AS source, b.id AS target
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
            {"source": row["source"], "target": row["target"]}
            for row in edge_rows
            if row["source"] and row["target"]
        ]
        return nodes, edges

    @staticmethod
    def _diff_node_sets(
        previous: GraphVersion | None,
        nodes: list[dict],
    ) -> tuple[int, int, int]:
        """与上一版本快照对比节点增减（set 差集，设计文档 7.1 Diff）。"""
        if previous is None:
            return len(nodes), 0, 0
        prev_ids = {n["id"] for n in (previous.snapshot_json or {}).get("nodes", [])}
        cur_ids = {n["id"] for n in nodes}
        added = len(cur_ids - prev_ids)
        removed = len(prev_ids - cur_ids)
        changed = len(cur_ids & prev_ids)
        return added, removed, changed

    def diff_versions(
        self,
        version_a: str,
        version_b: str,
    ) -> dict:
        """对比两个版本快照的差异。

        Returns:
            {"nodes_added": [...], "nodes_removed": [...], "nodes_changed": [...],
             "edges_added": [...], "edges_removed": [...]}
        """
        raise NotImplementedError("版本 Diff 已由 /evolution/diff 端点直接实现，无需走管理器")

    def list_versions(self, limit: int = 30) -> list[GraphVersionMeta]:
        """列出最近的图谱版本。"""
        raise NotImplementedError("版本列表已由 /evolution/versions 端点直接实现，无需走管理器")
