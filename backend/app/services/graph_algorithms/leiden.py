"""Leiden 技能簇识别（图算法优化方案阶段二）。

与 Louvain 同签名：`leiden(graph, resolution=1.0) -> dict[str, int]`
（skill_id → cluster_id，0 起始，`_reindex` 保证确定性输出）。

基于 python-igraph + leidenalg（seed=0 固定确定性），resolution 参数直接
映射 RBConfigurationVertexPartition 的 resolution_parameter（与 louvain 的
γ 语义一致：>1 细簇 / <1 粗簇 / 1.0 标准）。igraph/leidenalg 不可用时
ImportError 由调用方（API 层）回退 louvain，configs/graph_algo.yaml 的
`algorithm` 字段一行回滚。

验收口径（方案 §1.2）：Leiden 在 Q 与同质性两项均不劣于 Louvain 最优配置
（Q ≥ +0.01、同质性 ≥ +0.05）才允许切换——Q/同质性均用
app.services.graph_algorithms.louvain 的公开指标函数统一计算（同口径对比）。
"""

import igraph as ig
import leidenalg as la

from .louvain import _reindex  # 复用 louvain 的确定性簇 ID 重编号（同包共享）


def leiden(graph: dict[str, dict[str, float]], resolution: float = 1.0) -> dict[str, int]:
    """技能共现图的 Leiden 社区划分（skill_id → cluster_id，0 起始）。

    Args:
        graph: skill_id → {相邻 skill_id: 共现权重}（无向，双向登记）
        resolution: 分辨率参数 γ（与 louvain 语义一致，直接映射
            RBConfigurationVertexPartition.resolution_parameter）

    Returns:
        skill_id → cluster_id。空图返回空 dict，单节点自成一簇。
        依赖缺失（igraph/leidenalg 未安装）抛 ImportError，由调用方回退 louvain。
    """
    nodes = list(graph)
    if not nodes:
        return {}
    if len(nodes) == 1:
        return {nodes[0]: 0}

    index = {nd: i for i, nd in enumerate(nodes)}
    g = ig.Graph()
    g.add_vertices(len(nodes))
    edges: list[tuple[int, int]] = []
    weights: list[float] = []
    for u, nbs in graph.items():
        for v, w in nbs.items():
            if u < v:  # 无向边只登记一次（双向邻接表去重）
                edges.append((index[u], index[v]))
                weights.append(w)
    g.add_edges(edges)

    part = la.find_partition(
        g,
        la.RBConfigurationVertexPartition,
        weights=weights,
        resolution_parameter=resolution,
        seed=0,
    )
    return _reindex({nd: cid for nd, cid in zip(nodes, part.membership)})
