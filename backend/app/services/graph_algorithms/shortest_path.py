"""技能最短路径（设计文档 7.1 图算法应用）。

学习路径先修排序：`shortestPath((:Skill)-[*..6]-(:Skill))`。
图谱技能-技能无边，实际路径沿「岗位共现 + REQUIRES」边走（可能经过
Position 节点），返回的节点序列由前端按 type 区分展示。

查询走 Neo4j 核心 shortestPath（任意关系类型、深度 ≤6），失败返回 None，
调用方返回 404 而非 500（技能对不可达属正常数据状态）。
"""

from typing import Any, Optional


class Neo4jSessionLike:
    """仅依赖 run() 的 Neo4j 会话最小接口（便于测试注入桩）。"""

    def run(self, query: str, **params): ...


def shortest_path(
    session: Neo4jSessionLike,
    from_skill: str,
    to_skill: str,
    max_hops: int = 6,
) -> Optional[list[dict[str, Any]]]:
    """两技能间的最短路径（hop ≤ max_hops）。

    Args:
        session: Neo4j Session（或测试桩）
        from_skill: 起点技能 ID
        to_skill: 终点技能 ID
        max_hops: 最大跳数（设计文档 *..6）

    Returns:
        节点序列 [{id, name, type}]（含两端技能）；不存在可达路径或查询
        异常返回 None。
    """
    try:
        rows = session.run(
            """
            MATCH p = shortestPath((a:Skill {id: $from})-[*..$max_hops]-(b:Skill {id: $to}))
            RETURN [n IN nodes(p) | {id: n.id, name: n.name, type: labels(n)[0]}] AS path
            """,
            **{"from": from_skill},
            to=to_skill,
            max_hops=max_hops,
        )
    except Exception:
        # 查询异常（图谱不可达/无路径）统一视为不可达
        return None
    record = rows.single()
    if record is None:
        return None
    return list(record["path"])
