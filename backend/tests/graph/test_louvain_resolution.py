"""Louvain γ 参数化与调优脚本测试（图算法优化方案阶段一）。

覆盖：
- γ=1.0 与无参调用数学等价（向后兼容）
- γ > 1 产更细簇 / γ < 1 产更粗簇
- homogeneity 加权同质性手算值
- graph_algo_tune 的 filter_graph 对称性 / small_cluster_ratio / dry-run 冒烟
"""

import json

import pytest

from app.services.graph_algorithms.louvain import (
    _modularity,
    guard_community_distribution,
    homogeneity,
    louvain,
    louvain_hierarchical,
)
from scripts import graph_algo_tune as tune


# 两簇 + 桥节点的小图（簇 A: a-b-c 稠密，簇 B: d-e 稠密，桥 c-d 弱连）
_GRAPH = {
    "a": {"b": 3.0, "c": 3.0},
    "b": {"a": 3.0, "c": 3.0},
    "c": {"a": 3.0, "b": 3.0, "d": 1.0},
    "d": {"c": 1.0, "e": 2.0},
    "e": {"d": 2.0},
}


class TestResolutionCompatibility:
    def test_resolution_1_equals_default(self):
        """γ=1.0 与无参调用结果一致（默认值向后兼容）。"""
        for g in (_GRAPH, {"s1": {"s2": 1.0}, "s2": {"s1": 1.0}}, {"x": {"y": 5.0}, "y": {"x": 5.0}}):
            assert louvain(g, resolution=1.0) == louvain(g)

    def test_modularity_resolution_1_equals_standard(self):
        """Q 公式 γ=1.0 时等价标准模块度。"""
        partition = louvain(_GRAPH)
        q_std = 0.0
        # 用旧公式手算（γ 不参与）
        import app.services.graph_algorithms.louvain as louvain_mod

        communities: dict[int, set[str]] = {}
        for nd, cid in partition.items():
            communities.setdefault(cid, set()).add(nd)
        m = louvain_mod._total_weight(_GRAPH)
        for members in communities.values():
            sum_in = sum(w for nd in members for nb, w in _GRAPH[nd].items() if nb in members)
            sum_tot = sum(sum(_GRAPH[nd].values()) for nd in members)
            q_std += sum_in / (2 * m) - (sum_tot / (2 * m)) ** 2
        assert _modularity(_GRAPH, partition, 1.0) == pytest.approx(q_std, abs=1e-9)

    def test_resolution_affects_modularity(self):
        """γ 参与 Q 计算：γ 越大惩罚项越大（同划分 Q 单调递减）。"""
        partition = {"a": 0, "b": 0, "c": 0, "d": 1, "e": 1}
        q1 = _modularity(_GRAPH, partition, 0.5)
        q2 = _modularity(_GRAPH, partition, 1.0)
        q3 = _modularity(_GRAPH, partition, 2.0)
        assert q1 > q2 > q3


class TestResolutionGranularity:
    def test_higher_gamma_finer_clusters(self):
        """γ > 1 产更细簇（簇数不减少），γ < 1 产更粗簇（簇数不增加）。"""
        n_fine = len(set(louvain(_GRAPH, resolution=2.0).values()))
        n_default = len(set(louvain(_GRAPH, resolution=1.0).values()))
        n_coarse = len(set(louvain(_GRAPH, resolution=0.5).values()))
        # 细/默认/粗簇数单调：细 ≥ 默认 ≥ 粗
        assert n_fine >= n_default >= n_coarse
        # 桥节点在细 γ 下更可能拆出
        assert n_fine > n_coarse


class TestHomogeneity:
    def test_hand_computed_value(self):
        """手算：两簇各 2 节点 + 1 条跨簇弱边（权重 1），同质性 8/10 = 0.8。"""
        graph = {
            "a": {"b": 2.0, "c": 1.0},
            "b": {"a": 2.0},
            "c": {"a": 1.0, "d": 2.0},
            "d": {"c": 2.0},
        }
        partition = {"a": 0, "b": 0, "c": 1, "d": 1}
        # intra 双倍累计 = 2*(2+2) = 8，inter = 2*1 = 2
        assert homogeneity(graph, partition) == pytest.approx(8.0 / 10.0)

    def test_single_cluster_is_one(self):
        assert homogeneity(_GRAPH, {nd: 0 for nd in _GRAPH}) == pytest.approx(1.0)

    def test_empty_graph_zero(self):
        assert homogeneity({}, {}) == 0.0

    def test_isolated_nodes_zero(self):
        graph = {"x": {}, "y": {}}
        assert homogeneity(graph, {"x": 0, "y": 1}) == 0.0


class TestHierarchical:
    """阶段三：louvain_hierarchical 层次化提取。"""

    def test_levels_monotonically_coarser(self):
        """层级簇数单调不增（level 0 最细 → 逐层变粗），level 0 为每节点一簇。"""
        hier = louvain_hierarchical(_GRAPH)
        assert hier["levels"][0]["cluster_count"] == len(_GRAPH)
        counts = [lv["cluster_count"] for lv in hier["levels"]]
        assert counts == sorted(counts, reverse=True)

    def test_best_level_matches_louvain(self):
        """best_level 划分与 louvain() 输出一致（同口径模块度选择）。"""
        hier = louvain_hierarchical(_GRAPH)
        assert hier["membership"] == louvain(_GRAPH)
        best = next(lv for lv in hier["levels"] if lv["level"] == hier["best_level"])
        assert best["membership"] == louvain(_GRAPH)

    def test_levels_memberships_reindexed(self):
        """各层 membership 均 reindex（0..k-1 连续）。"""
        hier = louvain_hierarchical(_GRAPH)
        for lv in hier["levels"]:
            cids = sorted(set(lv["membership"].values()))
            assert cids == list(range(len(cids)))

    def test_empty_and_single(self):
        assert louvain_hierarchical({}) == {"levels": [], "best_level": None, "membership": {}}
        hier = louvain_hierarchical({"s1": {}})
        assert hier["best_level"] == 0
        assert hier["membership"] == {"s1": 0}
        assert hier["levels"][0]["cluster_count"] == 1

    def test_resolution_parameter_propagates(self):
        """resolution 传递到各层（γ 影响层级数量/模块度）。"""
        hier_fine = louvain_hierarchical(_GRAPH, resolution=2.0)
        hier_coarse = louvain_hierarchical(_GRAPH, resolution=0.5)
        # 细 γ 层级数不少于粗 γ（细 γ 下拆分更多轮）
        assert len(hier_fine["levels"]) >= len(hier_coarse["levels"])


class TestGuardCommunityDistribution:
    """社区层级写库前门禁：退化分布拒绝重建（P1）。"""

    @staticmethod
    def _levels(memberships: list[dict]) -> list[dict]:
        """构造 levels：每层 {level, membership}，level 从 0 递增。"""
        return [
            {"level": i, "membership": m, "modularity": 0.5,
             "cluster_count": len(set(m.values()))}
            for i, m in enumerate(memberships)
        ]

    def test_degenerate_single_cluster_rejected(self):
        # best_level 层仅 1 个社区（分辨率过低全并一簇）→ 拒绝
        levels = self._levels([
            {"a": 0, "b": 0, "c": 0},   # level 0
            {"a": 0, "b": 0, "c": 0},   # level 1（best，1 簇）
        ])
        with pytest.raises(ValueError, match="社区层级退化"):
            guard_community_distribution(levels, best_level=1)

    def test_dominant_cluster_rejected(self):
        # 最大社区占比 > 50%（单簇吞并）→ 拒绝
        levels = self._levels([
            {"a": 0, "b": 0, "c": 0, "d": 1, "e": 2},   # 3 簇，最大 3/5=60%
        ])
        with pytest.raises(ValueError, match="单簇吞并"):
            guard_community_distribution(levels, best_level=0)

    def test_healthy_distribution_passed(self):
        # 正常分布：≥3 簇、最大占比 ≤50% → 通过并返回摘要
        levels = self._levels([
            {"a": 0, "b": 0, "c": 1, "d": 2, "e": 3},   # 4 簇，最大 2/5=40%
        ])
        guard = guard_community_distribution(levels, best_level=0)
        assert guard["cluster_count"] == 4
        assert guard["dominant_ratio"] == 0.4

    def test_empty_levels_passed(self):
        guard = guard_community_distribution([])
        assert guard["levels"] == 0

    def test_best_level_none_fallback(self):
        # best_level 缺省：取中间层，不抛错
        levels = self._levels([
            {"a": 0, "b": 1},                   # level 0
            {"a": 0, "b": 1, "c": 2},           # level 1（中间层，3 簇）
        ])
        guard = guard_community_distribution(levels)
        assert guard["best_level"] == 1


class TestTuneHelpers:
    def test_filter_graph_symmetric(self):
        """filter_graph 保持无向对称（双向登记一致）。"""
        g = {
            "s1": {"s2": 3.0, "s3": 1.5},
            "s2": {"s1": 3.0},
            "s3": {"s1": 1.5},
        }
        f = tune.filter_graph(g, min_weight=2.0)
        assert f == {"s1": {"s2": 3.0}, "s2": {"s1": 3.0}}
        assert f["s1"]["s2"] == f["s2"]["s1"]

    def test_small_cluster_ratio(self):
        """过小簇占比口径：size ≤ 2 的簇占比。"""
        partition = {"a": 0, "b": 0, "c": 1, "d": 2, "e": 2, "f": 2}
        # 簇 0 大小 2（过小）、簇 1 大小 1（过小）、簇 2 大小 3（正常）
        assert tune.small_cluster_ratio(partition) == pytest.approx(2.0 / 3.0)
        assert tune.small_cluster_ratio({}) == 0.0

    def test_evaluate_dry_run(self):
        """evaluate 在当前配置下输出完整指标（dry-run 冒烟）。"""
        m = tune.evaluate(_GRAPH, resolution=1.0, min_weight=1.0)
        assert m["modularity"] > 0  # 两簇结构应有正模块度
        assert 0.0 <= m["homogeneity"] <= 1.0
        assert 0.0 <= m["small_ratio"] <= 1.0
        assert m["cluster_count"] >= 2

    def test_degenerate_penalty(self):
        """退化解防护：单簇/两簇/近单簇划分被判退化，objective 罚 0。

        2026-08-12 实跑发现：γ<1 时"全部合并"是分辨率化 Q 的退化最优
        （Q=1−γ 虚高 + 同质性恒 1.0），objective 必须屏蔽该解。
        """
        single = {nd: 0 for nd in _GRAPH}
        assert tune._is_degenerate(single) is True
        assert tune.score_partition(_GRAPH, single) == 0.0
        two_clusters = {"a": 0, "b": 0, "c": 0, "d": 1, "e": 1}
        assert tune._is_degenerate(two_clusters) is True
        # 近单簇（占比 > 0.5）也判退化
        near_single = {"a": 0, "b": 0, "c": 0, "d": 0, "e": 1}
        assert tune._is_degenerate(near_single) is True

    def test_score_uses_standard_modularity(self):
        """评分 Q 用标准模块度（γ 只生成划分、不参与评分）。"""
        from app.services.graph_algorithms.louvain import _modularity, homogeneity, louvain

        g = tune.filter_graph(_GRAPH, 1.0)
        partition = louvain(g)
        expected = (
            0.5 * _modularity(g, partition, 1.0)
            + 0.3 * homogeneity(g, partition)
            + 0.2 * (1.0 - tune.small_cluster_ratio(partition))
        )
        assert tune.score_partition(g, partition) == pytest.approx(expected)

    def test_tune_smoke_small_trials(self):
        """Optuna 小规模冒烟：8 trial 在 4 社区演示图上收敛（含退化解防护）。"""
        result = tune.tune(tune._DEMO_GRAPH, n_trials=8, n_runs=3)
        assert tune.GAMMA_MIN <= result["resolution"] <= tune.GAMMA_MAX
        assert tune.MIN_WEIGHT_MIN <= result["min_weight"] <= tune.MIN_WEIGHT_MAX
        assert result["stability_std"] == pytest.approx(0.0, abs=1e-9)  # 确定性算法
        # 最优解不允许是退化解（簇数 ≤ 2 或最大簇占比 > 0.5 会被罚 0）
        assert not result["metrics"]["degenerate"]
        assert result["objective"] > 0

    def test_export_roundtrip(self, tmp_path):
        """快照导出/加载往返一致（export 依赖 Neo4j，仅验证 payload 结构）。"""
        payload = {
            "exported_at": "2026-08-12T00:00:00",
            "node_count": 2,
            "edge_count": 1,
            "graph": {"s1": {"s2": 2.0}, "s2": {"s1": 2.0}},
            "name_map": {"s1": "Python", "s2": "Django"},
        }
        path = tmp_path / "snapshot.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        graph, name_map = tune.load_snapshot(path)
        assert graph == payload["graph"]
        assert name_map == payload["name_map"]
