"""PageRank 技能重要性排序（设计文档 7.1 图算法应用）。

技能网络 = 岗位共现（network.load_skill_cooccurrence 产物）。PageRank 用
幂迭代收敛（阻尼 0.85），无向共现边按两个有向边参与入边聚合。纯计算模块，
不依赖 Neo4j，便于单元测试。
"""

# 默认参数（设计文档 §7.1：gds.pageRank.stream() 的等价收敛策略）
DAMPING = 0.85
MAX_ITER = 50
TOL = 1e-6


def pagerank(
    graph: dict[str, dict[str, float]],
    damping: float = DAMPING,
    max_iter: int = MAX_ITER,
    tol: float = TOL,
) -> dict[str, float]:
    """技能共现图的 PageRank 分数（skill_id → score，归一化和为 1）。

    Args:
        graph: skill_id → {相邻 skill_id: 共现权重}。孤立节点（空邻居）
            按悬空处理；空图返回空 dict。
        damping: 阻尼系数（跳转概率 1-damping）
        max_iter: 幂迭代上限
        tol: 收敛阈值（L1 差）

    Returns:
        skill_id → 重要性分数（[0,1]，归一化）。
    """
    nodes = list(graph)
    n = len(nodes)
    if n == 0:
        return {}

    out_deg = {nd: len(nbs) for nd, nbs in graph.items()}
    pr = {nd: 1.0 / n for nd in nodes}
    for _ in range(max_iter):
        new_pr: dict[str, float] = {}
        # 悬空质量：无出边节点（孤立/无邻居）的分数均匀分配给全部节点
        dangling = sum(pr[nd] for nd in nodes if out_deg[nd] == 0)
        for nd in nodes:
            inbound = dangling / n + sum(
                pr[nb] / out_deg[nb] for nb in graph[nd] if out_deg[nb] > 0
            )
            new_pr[nd] = (1 - damping) / n + damping * inbound
        delta = max(abs(new_pr[nd] - pr[nd]) for nd in nodes)
        pr = new_pr
        if delta < tol:
            break
    return pr
