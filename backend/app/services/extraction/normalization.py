"""技能归一化（设计文档 5.3：SBERT 编码 → 层次聚类 → 人工词典兜底）。

与 dictionary.normalize_skill（词典快速路径，匹配引擎在线使用）不同，
本模块是离线规范化增强：对图谱全量技能名做 SBERT 层次聚类，将同义技能
归并到标准名（簇内频次最高者），输出 standard + confidence 供写回
`Skill.normalized_name`。

链路（scripts/sync_skill_normalization.py 调用）：
1. normalize_many：词典别名优先 → 剩余技能名 SBERT 层次聚类
   （聚合链接，余弦距离阈值 0.25，§5.3）
2. 簇内技能相似度 ≥ 0.85 → 建 `SIMILAR_TO` 关系（§5.3）
3. 模型不可用 → 退化为每项自成一簇（词典路径不阻塞）

工程约束：图谱技能数百个，O(N²) 距离矩阵用纯 Python 手写层次聚类
（不引入 scipy/networkx，numpy 为既有传递依赖），自写聚合链接。
"""

import re
from dataclasses import dataclass
from typing import Protocol

# §5.3 聚类距离阈值（余弦距离 = 1 - 相似度）
DISTANCE_THRESHOLD = 0.25
# §5.3 同簇内自动建 SIMILAR_TO 的相似度下限
SIMILAR_TO_THRESHOLD = 0.85

# 短英文词聚类保护（08-16）：纯英文 ≤6 字符全部跳过聚类。
# 实测 SBERT 对短英文缩写/产品名的嵌入虚高相似（Seata vs ADASIS 0.758、
# UART vs ADASIS 0.766、ArkUI 0.803——均 ≥ 0.75 聚类阈值），代表链接会把
# 无关缩写并簇（ADASIS 簇 60 个无关成员，08-16 链式漂移回归源）。
# 白名单词（Python/React 等）本就是标准名无需聚类；同义变体（Python3、
# "Python 3.0"、C++ 等）长度 >6 或含符号不保护，聚类合并仍生效。
_ABBREV_RE = re.compile(r"^[A-Za-z]{1,6}$")


def _should_protect(name: str) -> bool:
    """短英文词聚类保护判定：纯英文 ≤6 字符（缩写/产品名漂移源）。"""
    return bool(_ABBREV_RE.match(name))


class EmbedderLike(Protocol):
    """技能名 → 向量的最小接口（对齐 SkillEmbedder，便于测试桩注入）。"""

    def similarity(self, a: str, b: str) -> float: ...


@dataclass
class SkillNormResult:
    """单技能归一化结果。"""

    standard: str     # 标准技能名
    confidence: float  # 置信度（簇内相似度均值，词典命中为 1.0）


def _agglomerative_clusters(
    names: list[str],
    sim_fn,
    threshold: float = DISTANCE_THRESHOLD,
) -> list[list[str]]:
    """聚合链接层次聚类（自写，O(N³) 最坏/邻域启发收敛）。

    Args:
        names: 技能名列表（内部去重后按字典序排序再聚类，结果与输入顺序无关）
        sim_fn: (a, b) → 相似度 [0,1] 的可调用
        threshold: 距离阈值（相似度 ≥ 1 - threshold 的项合并）

    Returns:
        簇列表（每簇一个技能名列表）。逐项贪心合并：与已有簇**种子**（簇内
        首个成员，即字典序最小者）相似度达阈值即并入该簇（按首个命中簇），
        否则自成一簇。

    注意：使用**代表链接（与种子比较）而非单链接（与任一成员比较）**——
    单链接会沿相似度链漂移（2D可视化→3D→3D建模→建模→…→文心一言），
    把语义无关技能并入同一簇（08-13 实测 1185 个技能被并进"2D可视化"簇）。
    与种子比较切断链式传播：新名字必须与簇代表足够相似才并入。
    """
    # 先去重（同名项只参与一次合并），再排序固定簇种子与"首个命中簇"归属：
    # 输入顺序漂移（Neo4j 读取无 ORDER BY）会导致跨簇桥节点归入不同簇，
    # 排序保证同一名称集合的聚类结果唯一确定。
    ordered = sorted(set(names))
    clusters: list[list[str]] = []
    for name in ordered:
        merged = False
        for cluster in clusters:
            # 代表链接：仅与簇种子比较（单链接 any() 会链式漂移，见 docstring）
            if sim_fn(name, cluster[0]) >= 1 - threshold:
                cluster.append(name)
                merged = True
                break
        if not merged:
            clusters.append([name])
    return clusters


# ============================================================
# 写回前门禁（P0：防聚类异常污染图谱）
# ============================================================

# 巨型簇判定：任一标准名下成员数超过该值即视为链式漂移/聚类异常。
# 正常语义簇（AI/Python/深度学习）成员数不超过几十（08-13 修复后全量
# 6593 技能最大簇 AI=106）；漂移簇可达上千（2D可视化 1185 / 3A 1169）。
# 取 max(绝对下限, 总数×2%) 自适应图规模：小图 ≥50、大图按比例放宽。
_MEGA_CLUSTER_ABS_MIN = 50
_MEGA_CLUSTER_RATIO = 0.02
# 映射率边界：standard ≠ 原名 的比例。全自指（< 下限，聚类失效）与
# 过度合并（> 上限，漂移）均为异常信号。
_MAPPED_RATIO_MIN = 0.05
_MAPPED_RATIO_MAX = 0.90


def guard_cluster_distribution(normalized: dict[str, SkillNormResult]) -> dict:
    """归一化结果写回前门禁：簇分布异常直接拒绝（防污染入库）。

    08-13 事故：单链接链式漂移把 1185 个无关技能并入"2D可视化"簇，
    4610/6593 技能被错误归一化后直接写库。门禁在写回前拦截同类异常：

    1. **巨型簇检测**（主信号）：任一标准名成员数 > max(50, 总数×2%)
       判定异常——正常语义簇不会超过几十成员，漂移簇可达上千
    2. **映射率边界**（辅助信号）：映射比例 < 5%（聚类完全失效）或
       > 90%（过度合并）均判定异常

    Args:
        normalized: normalize_many 的输出 {原名: SkillNormResult}

    Returns:
        统计摘要（供日志/告警）：{total, mapped_ratio, max_cluster,
        max_standard}

    Raises:
        ValueError: 分布异常（调用方必须拒绝写库）
    """
    from collections import Counter

    total = len(normalized)
    if total == 0:
        return {"total": 0, "mapped_ratio": 0.0, "max_cluster": 0, "max_standard": ""}

    cnt = Counter(r.standard for r in normalized.values())
    max_cluster = max(cnt.values())
    max_standard = max(cnt, key=cnt.get)
    mapped = sum(1 for n, r in normalized.items() if r.standard != n)
    mapped_ratio = mapped / total

    cluster_limit = max(_MEGA_CLUSTER_ABS_MIN, int(total * _MEGA_CLUSTER_RATIO))
    if max_cluster > cluster_limit:
        raise ValueError(
            f"巨型簇异常：{max_cluster} 个技能并入标准名 {max_standard!r} "
            f"（上限 {cluster_limit}，疑似聚类链式漂移），拒绝写回"
        )
    if not (_MAPPED_RATIO_MIN <= mapped_ratio <= _MAPPED_RATIO_MAX):
        raise ValueError(
            f"归一化映射率异常：{mapped_ratio:.1%}（{mapped}/{total}）"
            f"超出 [{_MAPPED_RATIO_MIN:.0%}, {_MAPPED_RATIO_MAX:.0%}]，拒绝写回"
        )
    return {
        "total": total,
        "mapped_ratio": round(mapped_ratio, 4),
        "max_cluster": max_cluster,
        "max_standard": max_standard,
    }


class SkillNormalizer:
    """SBERT 层次聚类 + 词典兜底的技能归一化器。

    Args:
        embedder: 提供 similarity() 的嵌入器（缺省用 SkillEmbedder 单例）
        alias_map: 别名词典（缺省用 dictionary.SKILL_ALIAS），别名优先
        distance_threshold: 聚类距离阈值（§5.3 默认 0.25）
    """

    def __init__(
        self,
        embedder: EmbedderLike | None = None,
        alias_map: dict[str, str] | None = None,
        distance_threshold: float = DISTANCE_THRESHOLD,
    ) -> None:
        if embedder is None:
            from app.services.matching.semantic import SkillEmbedder

            embedder = SkillEmbedder.get()
        self._embedder = embedder
        self._alias = alias_map if alias_map is not None else _default_alias()
        self._threshold = distance_threshold

    def normalize_many(self, names: list[str]) -> dict[str, SkillNormResult]:
        """批量归一化：词典别名优先，剩余走 SBERT 聚类。

        Returns:
            {原始名: SkillNormResult}。模型不可用时退化为每项自成一簇
            （standard=自身，confidence=1.0），不抛错。
        """
        cleaned = [n.strip() for n in names if n and n.strip()]
        result: dict[str, SkillNormResult] = {}

        # 词典命中：直接映射，不参与聚类
        clustered_pool: list[str] = []
        for name in cleaned:
            standard = self._alias.get(name) or self._alias.get(name.lower())
            if standard:
                result[name] = SkillNormResult(standard=standard, confidence=1.0)
            elif _should_protect(name):
                # 短英文词聚类保护（08-16）：非白名单缩写/产品名 SBERT 嵌入
                # 虚高相似（Seata vs ADASIS 0.758 实测），聚类会把无关缩写
                # 并簇——跳过聚类保持原名（自成一簇语义）
                result[name] = SkillNormResult(standard=name, confidence=1.0)
            else:
                clustered_pool.append(name)

        # 未命中词典的走聚类（模型不可用：退化为自成一簇）
        clusters = self._cluster(clustered_pool)

        # 簇代表 = 频次最高者（同一技能名可能重复出现，count 加权）
        from collections import Counter

        freq = Counter(clustered_pool)
        for cluster in clusters:
            if len(cluster) == 1:
                name = cluster[0]
                result[name] = SkillNormResult(standard=name, confidence=1.0)
                continue
            # 簇内相似度均值作为置信度（代表与其余成员的均值）
            standard = max(cluster, key=lambda n: freq[n])
            members = [n for n in cluster if n != standard]
            conf = 0.0
            for member in members:
                try:
                    conf += self._embedder.similarity(standard, member)
                except Exception:
                    conf += 1.0 - self._threshold  # 模型中途不可用按阈值下限
            conf = (conf / len(members)) if members else 1.0
            for name in cluster:
                result[name] = SkillNormResult(standard=standard, confidence=round(conf, 4))
        return result

    def _cluster(self, names: list[str]) -> list[list[str]]:
        """层次聚类（模型不可用降级自成一簇）。"""
        if not names:
            return []
        try:
            return _agglomerative_clusters(names, self._embedder.similarity, self._threshold)
        except Exception:
            return [[n] for n in names]

    def similar_pairs(self, normalized: dict[str, SkillNormResult], threshold: float = SIMILAR_TO_THRESHOLD) -> list[tuple[str, str, float]]:
        """同簇内相似度 ≥ 0.85 的技能对（供写回 SIMILAR_TO 关系）。

        Args:
            normalized: normalize_many 的输出（standard 相同的归为一组）
            threshold: 相似度下限（§5.3 默认 0.85）

        Returns:
            [(技能 A, 技能 B, 相似度)]，仅同标准名组内、且非自指。
        """
        groups: dict[str, list[str]] = {}
        for name, res in normalized.items():
            if name == res.standard:
                continue  # 标准名自身不建自指关系
            groups.setdefault(res.standard, []).append(name)

        pairs: list[tuple[str, str, float]] = []
        for standard, members in groups.items():
            for member in members:
                try:
                    sim = self._embedder.similarity(standard, member)
                except Exception:
                    continue  # 模型中途不可用，该对跳过（不建边）
                if sim >= threshold:
                    pairs.append((standard, member, round(sim, 4)))
        return pairs


def _default_alias() -> dict[str, str]:
    """默认别名词典（延迟 import，避免循环依赖）。"""
    from app.services.extraction.dictionary import SKILL_ALIAS

    return SKILL_ALIAS
