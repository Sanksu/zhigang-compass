"""技能共现网络（图算法数据源，设计文档 7.1 图算法应用）。

图谱无技能-技能边（SIMILAR_TO / PREREQUISITE_OF 均未建），技能网络以
「岗位共现」构建：两技能被同一岗位 REQUIRES 即连边，权重 = 共现岗位数。
供 PageRank / Louvain / 最短路径三类图算法消费。

查询失败（Neo4j 不可达等）返回空结构，由调用方判空展示，不抛错——
与图谱其他端点降级语义一致（panorama 等依赖 neo4j_driver 时会失败）。
"""

from typing import Protocol


class Neo4jSessionLike(Protocol):
    """仅依赖 run() 的 Neo4j 会话最小接口（便于测试注入桩）"""

    def run(self, query: str, **params): ...


# 共现边权重因子（P0 改造，2026-08-08）：
# 技能共现权重按 REQUIRES 边必要性加权，突出核心技能组合、压低跨域噪声。
# 同一岗位同时要求两技能时：
#   - must+must：核心技能组合，权重 1.0（强连接）
#   - must+nice：主次组合，权重 0.5
#   - nice+nice：边缘组合（如算法岗同收前端/后端 nice），权重 0.2（弱连接）
# 配套提高 min_weight，使 nice-nice 大量弱边（当前 19 万对）被过滤，
# 跨域技能不再被低频共现强耦合，Louvain 聚类簇内同质性显著提升。
_COOCCUR_MUST_MUST = 1.0
_COOCCUR_MUST_NICE = 0.5
_COOCCUR_NICE_NICE = 0.2


def _combo_weight(n1: str | None, n2: str | None) -> float:
    """两技能边必要性组合 → 共现权重因子。necessity 缺失按 nice 处理（保守降权）。"""
    must = {"must"}
    nice = {"nice", None}
    s1 = "must" if n1 in must else "nice"
    s2 = "must" if n2 in must else "nice"
    if s1 == "must" and s2 == "must":
        return _COOCCUR_MUST_MUST
    if s1 != s2:
        return _COOCCUR_MUST_NICE
    return _COOCCUR_NICE_NICE


def load_skill_cooccurrence(
    session: Neo4jSessionLike,
    min_weight: float = 2.0,
) -> tuple[dict[str, dict[str, float]], dict[str, str]]:
    """从 Neo4j 读取技能共现图（P0 改造：按必要性加权）。

    Args:
        session: Neo4j Session（测试桩）
        min_weight: 共现边权重下限。权重 = 必要性组合因子 × 共现岗位数
            （must-must=1.0/必须、must-nice=0.5、nice-nice=0.2，再 × 共现数）。
            实测最优 2.0：过滤 must-nice 低频与全部 nice-nice 弱边，保留
            核心技能组合（must-must 共现 ≥2 或 must-nice 共现 ≥4），
            Louvain 聚类簇内同质性最佳。

    Returns:
        (graph, name_map)：
        - graph: skill_id → {相邻 skill_id: 加权共现权重}（无向边双向登记）
        - name_map: skill_id → skill_name
    """
    graph: dict[str, dict[str, float]] = {}
    name_map: dict[str, str] = {}
    try:
        rows = session.run(
            """
            MATCH (a:Skill)<-[r1:REQUIRES]-(p:Position)-[r2:REQUIRES]->(b:Skill)
            WHERE a.id < b.id
            RETURN a.id AS source, a.name AS source_name,
                   b.id AS target, b.name AS target_name,
                   r1.necessity AS n1, r2.necessity AS n2,
                   count(DISTINCT p) AS co_occur_count
            """
        )
        for rec in rows:
            # 权重 = 必要性组合因子 × 该技能对的独立共现岗位数（count DISTINCT p）。
            # 按技能对分组后，weight 反映"这两技能被多少岗位共同要求"，
            # 高频核心组合（如 Python+Java 被大量岗位同时 must）权重高，跨域
            # nice-nice 弱边权重低（0.2×低频）被 min_weight 过滤。
            weight = _combo_weight(rec["n1"], rec["n2"])
            weight *= float(rec["co_occur_count"] or 1)
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
