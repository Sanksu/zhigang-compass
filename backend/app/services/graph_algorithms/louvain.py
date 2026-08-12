"""Louvain 技能簇识别（设计文档 7.1 图算法应用）。

标准两阶段 Louvain（Blondel et al.）：
1. 阶段一：每个节点初始自成一簇，反复将节点移动到使模块度增量 ΔQ 最大
   的邻居社区，直至无正增益移动；
2. 阶段二：将社区聚合为超节点，在新图上重跑阶段一，迭代至模块度不再增长。

模块度增量公式（无向有权图，resolution 化）：
    ΔQ(C) = k_i,in / m - γ · (Σ_tot(C) · k_i) / (2m²)
其中 k_i,in 为节点 i 到社区 C 的连边权重和，Σ_tot(C) 为社区 C 节点连边
权重和，k_i 为节点 i 连边权重和，m 为全图权重和，γ 为分辨率参数
（图算法优化方案阶段一：γ > 1 产更细簇，γ < 1 产更粗簇，γ = 1.0 等价标准
Louvain，默认值保持向后兼容）。

纯标准库实现，零第三方依赖。
"""


def _total_weight(graph: dict[str, dict[str, float]]) -> float:
    """全图权重和 m（无向边在邻接表登记两次，除以 2 为真实权重和）。"""
    return sum(w for nbs in graph.values() for w in nbs.values()) / 2


def _modularity(graph: dict[str, dict[str, float]], partition: dict[str, int], resolution: float = 1.0) -> float:
    """划分的模块度 Q = Σ_c [ Σ_in/(2m) - γ·(Σ_tot/(2m))² ]。"""
    m = _total_weight(graph)
    if m == 0:
        return 0.0
    communities: dict[int, set[str]] = {}
    for nd, cid in partition.items():
        communities.setdefault(cid, set()).add(nd)
    q = 0.0
    for members in communities.values():
        sum_in = sum(
            w for nd in members for nb, w in graph[nd].items() if nb in members
        )
        sum_tot = sum(sum(graph[nd].values()) for nd in members)
        q += sum_in / (2 * m) - resolution * (sum_tot / (2 * m)) ** 2
    return q


def homogeneity(graph: dict[str, dict[str, float]], partition: dict[str, int]) -> float:
    """加权簇内同质性：Σ_c 簇内边权重 / Σ_c (簇内 + 簇间) 边权重。

    无向图双向登记下 intra/inter 均双倍累计，比值不变。值域 [0, 1]，
    1.0 表示簇内完全无外部连边（理想聚类）。图算法优化方案阶段一
    Optuna objective 的 0.3 权重项。
    """
    if not graph or not partition:
        return 0.0
    communities: dict[int, set[str]] = {}
    for nd, cid in partition.items():
        communities.setdefault(cid, set()).add(nd)
    intra = 0.0
    inter = 0.0
    for members in communities.values():
        for nd in members:
            for nb, w in graph[nd].items():
                if nb in members:
                    intra += w
                else:
                    inter += w
    total = intra + inter
    if total == 0:
        return 0.0
    return intra / total


def _phase1(graph, partition, resolution: float) -> bool:
    """局部移动：遍历节点，有正增益邻居社区则移动，返回是否发生移动。

    社区统计（Σ_tot / Σ_in）随移动实时维护，单轮内按出现顺序串行更新。
    """
    moved = False
    m = _total_weight(graph)
    if m == 0:
        return False

    k = {nd: sum(nbs.values()) for nd, nbs in graph.items()}
    comm_tot: dict[int, float] = {}
    comm_in: dict[int, float] = {}
    for nd, cid in partition.items():
        comm_tot[cid] = comm_tot.get(cid, 0.0) + k[nd]
        comm_in[cid] = comm_in.get(cid, 0.0) + sum(
            w for nb, w in graph[nd].items() if partition[nb] == cid
        )
    for cid in comm_in:
        comm_in[cid] /= 2  # 社区内边双向登记，减半避免重复计入

    for nd in graph:
        cid0 = partition[nd]
        # 节点 i 到各邻居社区的连边权重和 k_i,in
        ki_in: dict[int, float] = {}
        for nb, w in graph[nd].items():
            if nb != nd:
                ki_in[partition[nb]] = ki_in.get(partition[nb], 0.0) + w

        # 从原社区移除（还原去节点后的社区统计）
        comm_tot[cid0] -= k[nd]
        comm_in[cid0] = max(0.0, comm_in[cid0] - ki_in.get(cid0, 0.0))

        best_cid, best_gain = cid0, 0.0
        for cid, w_in in ki_in.items():
            if cid == cid0:
                continue  # 移回原社区为无操作
            gain = w_in / m - resolution * (comm_tot[cid] * k[nd]) / (2 * m * m)
            if gain > best_gain:
                best_cid, best_gain = cid, gain

        if best_cid != cid0 and best_gain > 0:
            moved = True
            partition[nd] = best_cid
            comm_tot[best_cid] = comm_tot.get(best_cid, 0.0) + k[nd]
            comm_in[best_cid] = comm_in.get(best_cid, 0.0) + ki_in.get(best_cid, 0.0)
        else:
            # 未移动：节点回归原社区
            comm_tot[cid0] += k[nd]
            comm_in[cid0] = comm_in.get(cid0, 0.0) + ki_in.get(cid0, 0.0)
    return moved


def _aggregate(graph, partition, members) -> tuple[dict, dict]:
    """社区聚合：同簇节点合并为超节点，输出新图 + 超节点→原图节点成员表。"""
    clusters: dict[int, list[str]] = {}
    for nd, cid in partition.items():
        clusters.setdefault(cid, []).append(nd)
    cids = sorted(clusters)
    index = {cid: i for i, cid in enumerate(cids)}

    agg: dict[str, dict[str, float]] = {f"c{i}": {} for i in range(len(cids))}
    agg_members: dict[str, list[str]] = {}
    for cid in cids:
        i = index[cid]
        key = f"c{i}"
        agg_members[key] = [orig for nd in clusters[cid] for orig in members[nd]]
        weights: dict[str, float] = {}
        for nd in clusters[cid]:
            for nb, w in graph[nd].items():
                j = index[partition[nb]]
                if j == i:
                    continue  # 社区内边不进聚合图
                nkey = f"c{j}"
                weights[nkey] = weights.get(nkey, 0.0) + w
        agg[key] = weights
    return agg, agg_members


def louvain(graph: dict[str, dict[str, float]], resolution: float = 1.0) -> dict[str, int]:
    """技能共现图的社区划分（skill_id → cluster_id，0 起始）。

    Args:
        graph: skill_id → {相邻 skill_id: 共现权重}（无向，双向登记）
        resolution: 分辨率参数 γ（图算法优化方案阶段一）。γ > 1 产更细簇，
            γ < 1 产更粗簇，默认 1.0 等价标准 Louvain（向后兼容）

    Returns:
        skill_id → cluster_id。空图返回空 dict，单节点自成一簇。
    """
    nodes = list(graph)
    if not nodes:
        return {}
    if len(nodes) == 1:
        return {nodes[0]: 0}

    current: dict[str, dict[str, float]] = graph
    members: dict[str, list[str]] = {nd: [nd] for nd in nodes}
    partition = {nd: i for i, nd in enumerate(nodes)}

    best_flat = dict(partition)
    best_q = _modularity(graph, partition, resolution)

    for _ in range(32):  # 迭代上限（正常 2-5 轮收敛）
        moved = _phase1(current, partition, resolution)
        # 扁平化：聚合节点成员 → 原图节点簇映射，用原图算模块度
        flat: dict[str, int] = {}
        for nd, cid in partition.items():
            for orig in members[nd]:
                flat[orig] = cid
        q = _modularity(graph, flat, resolution)
        if q > best_q + 1e-9:
            best_flat, best_q = flat, q
        if not moved:
            break
        agg, agg_members = _aggregate(current, partition, members)
        if len(agg) <= 1:
            break
        current, members = agg, agg_members
        partition = {nd: i for i, nd in enumerate(current)}

    return _reindex(best_flat)


def _reindex(partition: dict[str, int]) -> dict[str, int]:
    """簇 ID 重新编号为 0..k-1，保持确定性输出。"""
    mapping: dict[int, int] = {}
    result: dict[str, int] = {}
    for nd, cid in partition.items():
        if cid not in mapping:
            mapping[cid] = len(mapping)
        result[nd] = mapping[cid]
    return result
