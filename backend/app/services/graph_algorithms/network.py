"""技能共现网络（图算法数据源，设计文档 7.1 图算法应用）。

图谱无技能-技能边（SIMILAR_TO / PREREQUISITE_OF 均未建），技能网络以
「岗位共现」构建：两技能被同一岗位 REQUIRES 即连边，权重 = 共现岗位数。
供 PageRank / Louvain / 最短路径三类图算法消费。

查询失败（Neo4j 不可达等）返回空结构，由调用方判空展示，不抛错——
与图谱其他端点降级语义一致（panorama 等依赖 neo4j_driver 时会失败）。
"""

from typing import Protocol


class Neo4jSessionLike(Protocol):
    """仅依赖 run() 的 Neo4j 会话最小接口（便于测试注入桩）。"""

    def run(self, query: str, **params): ...


def load_skill_cooccurrence(
    session: Neo4jSessionLike,
    min_weight: float = 1.0,
) -> tuple[dict[str, dict[str, float]], dict[str, str]]:
    """从 Neo4j 拉取技能共现网络。

    Args:
        session: Neo4j Session（或测试桩）
        min_weight: 共现边权重下限（共现岗位数），过滤低频共现边

    Returns:
        (graph, name_map)：
        - graph: skill_id → {相邻 skill_id: 共现岗位数}（无向边双向登记）
        - name_map: skill_id → skill_name
    """
    graph: dict[str, dict[str, float]] = {}
    name_map: dict[str, str] = {}
    try:
        rows = session.run(
            """
            MATCH (a:Skill)<-[:REQUIRES]-(p:Position)-[:REQUIRES]->(b:Skill)
            WHERE a.id < b.id
            RETURN a.id AS source, a.name AS source_name,
                   b.id AS target, b.name AS target_name,
                   count(p) AS weight
            """
        )
        for rec in rows:
            weight = float(rec["weight"] or 1)
            if weight < min_weight:
                continue
            source, target = rec["source"], rec["target"]
            graph.setdefault(source, {})[target] = weight
            graph.setdefault(target, {})[source] = weight
            name_map.setdefault(source, rec.get("source_name") or source)
            name_map.setdefault(target, rec.get("target_name") or target)
    except Exception:
        # 图谱不可达：返回空结构，调用方展示空结果而非 500
        return {}, {}
    return graph, name_map
