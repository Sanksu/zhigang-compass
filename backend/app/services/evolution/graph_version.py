"""图谱版本与快照管理接口（设计文档 7.1 节版本管理）。

每次演化生成 `graph_v{date}.json` 全量快照（APOC），存入 PostgreSQL
`graph_versions.snapshot_json` JSONB 字段。版本快照保留 90 天，支持历史回溯对比。
"""

from app.services.evolution.schemas import GraphVersionMeta


class GraphVersionManager:
    """图谱版本管理器接口。

    M3 实现：
    - APOC 全量快照导出
    - Diff 对比（set 差集计算新增/删除/变化节点）
    - 90 天保留策略
    - T+1 更新：每日 05:00 前发布新版本
    """

    def create_snapshot(self, triggered_by: str = "scheduled") -> GraphVersionMeta:
        """生成图谱快照并写入 PostgreSQL。"""
        raise NotImplementedError("图谱快照实现将在 M3 由算法岗完成")

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
        raise NotImplementedError("版本 Diff 实现将在 M3 由算法岗完成")

    def list_versions(self, limit: int = 30) -> list[GraphVersionMeta]:
        """列出最近的图谱版本。"""
        raise NotImplementedError("版本列表实现将在 M3 由算法岗完成")
