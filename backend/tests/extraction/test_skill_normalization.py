"""技能归一化测试（设计文档 5.3：SBERT 层次聚类 + 词典兜底）。

覆盖：
- 层次聚类：同义技能合并、阈值边界、异簇分离
- 词典优先：别名命中不参与聚类
- normalize_many：簇代表选择、置信度计算、模型不可用降级
- similar_pairs：SIMILAR_TO 阈值过滤、自指排除
"""

import pytest

from app.services.extraction.normalization import (
    DISTANCE_THRESHOLD,
    SIMILAR_TO_THRESHOLD,
    SkillNormalizer,
    SkillNormResult,
    _agglomerative_clusters,
)


class _FakeEmbedder:
    """按预置相似度矩阵返回相似度的桩（规避真实 SBERT 加载）。"""

    def __init__(self, sims: dict[tuple[str, str], float]):
        self._sims = sims

    def similarity(self, a: str, b: str) -> float:
        return self._sims.get((a, b), self._sims.get((b, a), 0.0))


def _python_sim_embedder():
    """按小写前缀聚类的桩：同前缀相似 0.9，否则 0.1。"""
    class _PrefixEmbedder:
        def similarity(self, a, b):
            return 0.9 if a.lower()[:3] == b.lower()[:3] else 0.1
    return _PrefixEmbedder()


# ============================================================
# 层次聚类
# ============================================================

class TestAgglomerativeClusters:
    def test_similar_merge(self):
        # 前缀相同（相似 0.9 ≥ 1-0.25=0.75）→ 合并同簇
        names = ["React", "React.js", "Vue.js", "Vue"]
        clusters = _agglomerative_clusters(names, _python_sim_embedder().similarity)
        # "React" 与 "React.js" 同簇；"Vue.js" 与 "Vue" 同簇
        flat = sorted([sorted(c) for c in clusters], key=len, reverse=True)
        assert any("React" in c and "React.js" in c for c in flat)
        assert any("Vue" in c and "Vue.js" in c for c in flat)

    def test_threshold_boundary(self):
        # 相似度恰为 0.75 = 1-0.25 → 合并（>= 1-threshold）
        embedder = _FakeEmbedder({("A", "B"): 0.75})
        clusters = _agglomerative_clusters(["A", "B"], embedder.similarity)
        assert len(clusters) == 1 and len(clusters[0]) == 2

    def test_dissimilar_separate(self):
        embedder = _FakeEmbedder({("A", "B"): 0.5})
        clusters = _agglomerative_clusters(["A", "B"], embedder.similarity)
        assert len(clusters) == 2  # 相似度不足阈值 → 各成一簇

    def test_single_name(self):
        clusters = _agglomerative_clusters(["Python"], _python_sim_embedder().similarity)
        assert clusters == [["Python"]]

    def test_order_independent(self):
        # 跨簇桥场景：B-D 相似（A-D/B-C 不相似），不同输入顺序下"并入首个命中簇"
        # 会产生不同划分（A 与 B 先遇 vs D 与 C 先遇）。排序后聚类结果应与输入顺序无关
        # （Neo4j 读取无 ORDER BY，防止结果随行序漂移）。
        embedder = _FakeEmbedder({
            ("A", "B"): 0.9,
            ("C", "D"): 0.9,
            ("B", "D"): 0.9,  # 跨簇桥
            ("A", "C"): 0.1,
            ("A", "D"): 0.1,
            ("B", "C"): 0.1,
        })
        c1 = _agglomerative_clusters(["A", "B", "C", "D"], embedder.similarity)
        c2 = _agglomerative_clusters(["D", "C", "B", "A"], embedder.similarity)
        norm = lambda cs: sorted(tuple(sorted(c)) for c in cs)
        assert norm(c1) == norm(c2)


# ============================================================
# SkillNormalizer
# ============================================================

class TestSkillNormalizer:
    def test_alias_priority(self):
        # 词典别名命中（"react" 小写别名），置信度 1.0，不参与聚类
        normalizer = SkillNormalizer(
            embedder=_python_sim_embedder(),
            alias_map={"react": "React", "vue": "Vue.js"},
        )
        result = normalizer.normalize_many(["react", "vue"])
        assert result["react"].standard == "React"
        assert result["react"].confidence == 1.0

    def test_cluster_representative(self):
        # 未命中词典走聚类：簇代表 = 频次最高者
        normalizer = SkillNormalizer(embedder=_python_sim_embedder(), alias_map={})
        # "PyTorch" 出现 2 次（频次最高）→ 为簇代表
        result = normalizer.normalize_many(["PyTorch", "Pytorch", "PyTorch"])
        assert result["Pytorch"].standard == "PyTorch"
        assert result["PyTorch"].standard == "PyTorch"

    def test_confidence_calculation(self):
        # 簇内成员相似度均值作为置信度（A 为代表，B/C 为成员）
        embedder = _FakeEmbedder({("A", "B"): 0.8, ("A", "C"): 0.6})
        normalizer = SkillNormalizer(embedder=embedder, alias_map={})
        result = normalizer.normalize_many(["A", "B", "C"])
        # 簇 [A,B]：A 为代表（频次并列取首），B 为唯一成员
        assert result["B"].standard == "A"
        assert result["B"].confidence == pytest.approx(0.8, abs=1e-4)

    def test_embedder_unavailable_fallback(self):
        # 模型不可用（抛异常）→ 每项自成一簇，不抛错
        class BrokenEmbedder:
            def similarity(self, a, b):
                raise RuntimeError("model down")

        normalizer = SkillNormalizer(embedder=BrokenEmbedder(), alias_map={})
        result = normalizer.normalize_many(["A", "B"])
        assert result["A"].standard == "A"
        assert result["B"].standard == "B"

    def test_empty_input(self):
        normalizer = SkillNormalizer(embedder=_python_sim_embedder(), alias_map={})
        assert normalizer.normalize_many([]) == {}
        assert normalizer.normalize_many(["  ", ""]) == {}


# ============================================================
# SIMILAR_TO 对生成
# ============================================================

class TestSimilarPairs:
    def test_threshold_filter(self):
        # 标准名 "Python"，成员相似 0.9（≥0.85）入对，0.5 不入
        embedder = _FakeEmbedder({("Python", "Python3"): 0.9, ("Python", "python"): 0.5})
        normalizer = SkillNormalizer(embedder=embedder, alias_map={})
        normalized = {
            "Python": SkillNormResult("Python", 1.0),
            "Python3": SkillNormResult("Python", 0.9),
            "python": SkillNormResult("Python", 0.5),
        }
        pairs = normalizer.similar_pairs(normalized)
        assert ("Python", "Python3", 0.9) in pairs
        assert all(p[2] >= 0.85 for p in pairs)
        assert not any(p[0] == p[1] for p in pairs)  # 无自指

    def test_standard_self_excluded(self):
        # 标准名自身不产生关系
        embedder = _FakeEmbedder({})
        normalizer = SkillNormalizer(embedder=embedder, alias_map={})
        normalized = {"Go": SkillNormResult("Go", 1.0)}
        assert normalizer.similar_pairs(normalized) == []

    def test_embedder_unavailable_skips_pairs(self):
        # 模型中途不可用：该对跳过（不建边、不抛错），其余对不受影响
        class BrokenEmbedder:
            def similarity(self, a, b):
                raise RuntimeError("model down")

        normalizer = SkillNormalizer(embedder=BrokenEmbedder(), alias_map={})
        normalized = {
            "Python3": SkillNormResult("Python", 0.9),
            "Python": SkillNormResult("Python", 1.0),
        }
        assert normalizer.similar_pairs(normalized) == []
