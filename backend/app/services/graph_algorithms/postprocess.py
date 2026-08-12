"""技能簇后处理与 LLM 兜底触发（图算法优化方案 §4）。

职责：对社区检测（Louvain/Leiden）输出的簇划分做规则优先后处理，
并把 LLM 兜底的**触发判断**独立成纯函数——是否调用 LLM 由调用方（API 层）
决定，本模块只输出触发标记，不直接调 LLM（保持可单测、可缓存）。

规则优先流程（§4.1，按序执行）：
1. 孤立簇剔除：簇内所有成员均无任何共现边（度为零）→ 标记 `orphan`，移出业务视图
2. 过小簇合并：簇成员 ≤ min_size 且无主导技能 → 并入共现权重最大的相邻簇；
   无可并入的相邻簇（全部孤立）保持原簇并标记
3. 簇标签：规则生成 = 簇内按共现权重 top-3 技能拼接（"Python·Django·PostgreSQL"）
4. LLM 触发标记：以下条件任一命中 → `needs_llm=True`（由调用方决定是否调用）
   - 无主导技能（top-1 权重占比 < DOMINANT_RATIO）
   - 簇内技能跨 ≥ LLM_CROSS_CATEGORY 个白名单类别
   - 规则标签为空（簇内无技能）

LLM 调用失败降级：调用方捕获 LLMExtractionError 后回退 `rule_label` 与
`coherent=True`（不阻塞 API），与 JD 抽取链路"LLM 失败规则兜底"同语义。

输入数据结构（与 louvain() 输出兼容）：
    clusters: skill_id → cluster_id
    skill_names: skill_id → 技能名
    weights: skill_id → {相邻 skill_id: 共现权重}（网络图，无向双向登记）
"""

from __future__ import annotations

from collections import Counter, defaultdict

# 过小簇合并阈值：簇成员数 ≤ 该值且无主导技能时并入相邻簇（§4.1 规则 2）
MIN_CLUSTER_SIZE = 2
# 主导技能判定阈值：top-1 权重占比 < 该值视为"无主导技能"（§4.2 触发条件）
DOMINANT_RATIO = 0.4
# LLM 触发：簇内技能跨 ≥ 该值个类别视为复合栈，需 LLM 裁决（§4.2 触发条件）
LLM_CROSS_CATEGORY = 3
# 规则标签保留的技能数上限（§4.1 规则 3）
LABEL_TOP_N = 3


def _intra_degrees(
    weights: dict[str, dict[str, float]],
    members: list[str],
) -> dict[str, float]:
    """簇内边度数：每个成员到其他成员的边权重和（跨簇边不计）。

    D3 修复：主导判定与标签排序若用全图度数，会因跨簇强边（如伪簇
    仅靠单条外部强边粘连）误判结构有效；簇内粘合度才是簇结构依据。
    """
    member_set = set(members)
    return {
        nd: sum(
            w for nb, w in (weights.get(nd) or {}).items() if nb in member_set
        )
        for nd in members
    }


def _has_dominant_skill(
    weights: dict[str, dict[str, float]],
    members: list[str],
) -> bool:
    """簇内是否存在主导技能（top-1 权重占比 ≥ DOMINANT_RATIO）。

    基于簇内边度数（_intra_degrees）：跨簇强边不构成簇内结构。
    单成员簇返回 False：无内部结构，天然无主导（§4.1 合并前置条件）。
    """
    if len(members) < 2:
        return False
    degree = _intra_degrees(weights, members)
    total = sum(degree.values())
    if total <= 0:
        return False
    top = max(degree.values())
    return top / total >= DOMINANT_RATIO


def _is_orphan_cluster(
    weights: dict[str, dict[str, float]],
    members: list[str],
) -> bool:
    """孤立簇：簇内所有成员均无任何共现边（含内部边，§4.1 规则 1）。

    与"无跨簇边"不同：多成员簇内部连通但无外部边是合法社区，不是孤立。
    孤立仅指节点本身无任何边（度为零）。
    """
    return all(not (weights.get(nd) or {}) for nd in members)


def _build_ext_tot(
    weights: dict[str, dict[str, float]],
    result: dict[str, int],
) -> dict[int, dict[int, float]]:
    """基于当前划分重算簇间外部共现总权重。

    逐轮重算（图 <1000 节点，代价可忽略）而非增量维护，消除两个隐患：
    - D1：无向边在遍历中被重复累加（双向登记 ×2）
    - D2：增量维护依赖 defaultdict 隐式重建已空簇条目，结构脆弱

    无向边只计一次：按 (nd, nb) 有序对去重。
    """
    ext: dict[int, dict[int, float]] = {}
    seen: set[tuple[str, str]] = set()
    for nd, nbs in weights.items():
        if nd not in result:
            continue
        for nb, w in nbs.items():
            if nb not in result or nd == nb:
                continue
            pair = (nd, nb) if nd <= nb else (nb, nd)
            if pair in seen:
                continue
            seen.add(pair)
            cid, cid_nb = result[nd], result[nb]
            if cid != cid_nb:
                # 先 setdefault 再读写：Python 赋值先求值 RHS，若把 setdefault
                # 放在等号左侧，RHS 求值时键尚不存在会 KeyError。
                ext.setdefault(cid, {})
                ext.setdefault(cid_nb, {})
                ext[cid][cid_nb] = ext[cid].get(cid_nb, 0.0) + w
                ext[cid_nb][cid] = ext[cid_nb].get(cid, 0.0) + w
    return ext


def _merge_small_clusters(
    clusters: dict[str, int],
    weights: dict[str, dict[str, float]],
    min_size: int = MIN_CLUSTER_SIZE,
) -> tuple[dict[str, int], set[int]]:
    """过小簇并入共现权重最大的相邻簇（§4.1 规则 2）。

    逐轮收敛：每轮从最新划分重算簇间外部共现权重，把第一个
    "成员 ≤ min_size 且无主导技能且存在跨簇边"的簇并入权重最大的
    相邻簇，直到无可并入的簇。逐轮重算保证合并目标不是另一个
    待合并簇（目标被吸收后其成员含并入者，下一轮一并迁移），
    且避免增量维护的翻倍累加与 defaultdict 隐式重建（D1/D2）。

    Returns:
        (新划分, 被合并簇 id 集合)。weights 中不在 clusters 的节点忽略。
    """
    result = dict(clusters)
    merged: set[int] = set()
    while True:
        ext_tot = _build_ext_tot(weights, result)
        cur: dict[int, list[str]] = defaultdict(list)
        for nd, cid in result.items():
            cur[cid].append(nd)
        action: tuple[int, list[str], int] | None = None
        for cid, members in cur.items():
            if len(members) > min_size:
                continue
            # 合并前置条件（§4.1 规则 2）：无主导技能才并入邻居簇。
            # 避免双技能强簇被单技能挂靠簇反向并走。
            if _has_dominant_skill(weights, members):
                continue
            nbs = ext_tot.get(cid)
            if not nbs:
                continue
            best_nb = max(nbs, key=nbs.get)
            action = (cid, members, best_nb)
            break
        if action is None:
            break
        cid, members, best_nb = action
        for m in members:
            result[m] = best_nb
        merged.add(cid)
    return result, merged


def _category_counts(
    skill_names: dict[str, str],
    members: list[str],
    categories: dict[str, str] | None,
) -> Counter:
    """簇内技能的白名单类别分布（LLM 跨类别触发依据）。

    同时支持两种 key（D4）：categories 以技能名为 key 时经 skill_names
    映射查找；以 skill_id 为 key 时直接命中。映射缺失时不静默失效。
    """
    if not categories:
        return Counter()
    counts: Counter = Counter()
    for nd in members:
        cat = categories.get(nd) or categories.get(skill_names.get(nd, ""))
        if cat:
            counts[cat] += 1
    return counts


def rule_label(
    members: list[str],
    skill_names: dict[str, str],
    weights: dict[str, dict[str, float]],
    top_n: int = LABEL_TOP_N,
) -> str:
    """规则标签：簇内按簇内共现权重 top-N 技能拼接（§4.1 规则 3）。

    度数只统计成员之间的边（_intra_degrees，与主导判定同口径）——
    跨簇强边不代表该技能在簇内的代表性。权重降序取前 top_n 个技能名
    （权重相同按技能名稳定排序），用 "·" 拼接；簇无成员返回空串。
    """
    degree = _intra_degrees(weights, members)
    ranked = sorted(members, key=lambda nd: (-degree[nd], nd))
    names = [skill_names.get(nd, nd) for nd in ranked[:top_n]]
    return "·".join(names)


class ClusterPostProcessor:
    """技能簇规则后处理（§4.1 规则优先流程）。

    输入 Louvain/Leiden 的扁平划分（skill_id → cluster_id），输出：
    - 孤立簇剔除（orphan 标记，移出业务视图）
    - 过小簇合并
    - 簇标签（规则）
    - LLM 触发标记（needs_llm）+ 触发原因

    纯同步、无 IO：输入含全部判定所需数据（weights/categories），
    便于单测与按簇缓存（簇不变则后处理结果可缓存）。
    """

    def __init__(
        self,
        *,
        min_cluster_size: int = MIN_CLUSTER_SIZE,
        dominant_ratio: float = DOMINANT_RATIO,
        cross_category: int = LLM_CROSS_CATEGORY,
    ):
        self.min_size = min_cluster_size
        self.dominant_ratio = dominant_ratio
        self.cross_category = cross_category

    def process(
        self,
        clusters: dict[str, int],
        weights: dict[str, dict[str, float]],
        skill_names: dict[str, str] | None = None,
        categories: dict[str, str] | None = None,
    ) -> dict:
        """执行规则优先后处理，返回结构化结果。

        Args:
            clusters: skill_id → cluster_id（louvain()/leiden() 输出）
            weights: skill_id → {相邻 skill_id: 权重}（共现网络，无向双向）
            skill_names: skill_id → 技能名（缺省用 id 本身）
            categories: 技能名 → 白名单类别（缺省不判跨类别触发）

        Returns:
            {
              "clusters": [{"cluster_id", "skills": [skill_id], "size",
                            "label", "needs_llm", "triggers": [str], "orphan"}],
              "merged": [被合并簇 id],          # 并入其他簇的小簇
              "orphaned": [孤立簇 id],           # 成员度为零的孤立簇
            }
        """
        names = skill_names or {}
        membership: dict[int, list[str]] = defaultdict(list)
        for nd, cid in clusters.items():
            membership[cid].append(nd)

        # 规则 1：孤立簇剔除（簇内所有成员无任何共现边 → orphan）
        orphan_ids: set[int] = set()
        for cid, members in membership.items():
            if _is_orphan_cluster(weights, members):
                orphan_ids.add(cid)

        # 规则 2：过小簇合并（孤立簇不进合并，保持原划分）
        merged_partition, merged_ids = _merge_small_clusters(
            {nd: cid for nd, cid in clusters.items() if cid not in orphan_ids},
            weights,
            min_size=self.min_size,
        )
        # 合并结果并入原划分（孤立簇不动）
        final: dict[str, int] = dict(clusters)
        for nd, cid in merged_partition.items():
            final[nd] = cid

        # 按最终划分重建成员关系，生成输出
        final_membership: dict[int, list[str]] = defaultdict(list)
        for nd, cid in final.items():
            final_membership[cid].append(nd)

        out_clusters = []
        for cid in sorted(final_membership):
            members = sorted(final_membership[cid])
            is_orphan = cid in orphan_ids
            label = "" if is_orphan else rule_label(members, names, weights)
            triggers: list[str] = []
            needs_llm = False

            if not is_orphan:
                # 触发条件 1：无主导技能
                if not _has_dominant_skill(weights, members):
                    triggers.append("no_dominant_skill")
                # 触发条件 2：跨类别过多
                if self.cross_category > 0:
                    cats = _category_counts(names, members, categories)
                    if len([c for c in cats.values() if c > 0]) >= self.cross_category:
                        triggers.append("cross_category")
                # 触发条件 3：规则标签为空
                if not label:
                    triggers.append("empty_label")
                needs_llm = bool(triggers)

            out_clusters.append({
                "cluster_id": cid,
                "skills": members,
                "size": len(members),
                "label": label,
                "needs_llm": needs_llm,
                "triggers": triggers,
                "orphan": is_orphan,
            })

        return {
            "clusters": out_clusters,
            "merged": sorted(merged_ids),
            "orphaned": sorted(orphan_ids),
        }
