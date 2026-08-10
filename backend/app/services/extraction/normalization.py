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

from dataclasses import dataclass
from typing import Protocol

# §5.3 聚类距离阈值（余弦距离 = 1 - 相似度）
DISTANCE_THRESHOLD = 0.25
# §5.3 同簇内自动建 SIMILAR_TO 的相似度下限
SIMILAR_TO_THRESHOLD = 0.85


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
        簇列表（每簇一个技能名列表）。逐项贪心合并：与已有簇中任一成员
        相似度达阈值即并入该簇（按首个命中簇），否则自成一簇。
    """
    # 先去重（同名项只参与一次合并），再排序固定簇种子与"首个命中簇"归属：
    # 输入顺序漂移（Neo4j 读取无 ORDER BY）会导致跨簇桥节点归入不同簇，
    # 排序保证同一名称集合的聚类结果唯一确定。
    ordered = sorted(set(names))
    clusters: list[list[str]] = []
    for name in ordered:
        merged = False
        for cluster in clusters:
            if any(sim_fn(name, member) >= 1 - threshold for member in cluster):
                cluster.append(name)
                merged = True
                break
        if not merged:
            clusters.append([name])
    return clusters


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
