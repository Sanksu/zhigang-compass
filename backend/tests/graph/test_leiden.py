"""Leiden 技能簇识别单元测试（图算法优化方案阶段二）。

覆盖：
- 同签名（graph, resolution）与 louvain 输出格式一致（skill_id → cluster_id）
- seed=0 固定确定性、空图/单节点
- 两社区小图划分正确、分辨率参数影响
- 依赖缺失（igraph/leidenalg 未安装）抛 ImportError 由调用方回退
"""

import pytest

from app.services.graph_algorithms.leiden import leiden


# 两社区 + 弱桥边（与 louvain 测试同构，便于对比）
_TWO_COMMUNITY = {
    "a": {"b": 3.0, "c": 3.0, "d": 1.0},
    "b": {"a": 3.0, "c": 3.0},
    "c": {"a": 3.0, "b": 3.0},
    "d": {"a": 1.0, "e": 2.0},
    "e": {"d": 2.0},
}


class TestLeiden:
    def test_two_community_structure(self):
        """两社区小图：a-b-c 与 d-e 应分属两簇。"""
        partition = leiden(_TWO_COMMUNITY)
        assert set(partition) == set(_TWO_COMMUNITY)
        assert partition["a"] == partition["b"] == partition["c"]
        assert partition["d"] == partition["e"]
        assert partition["a"] != partition["d"]

    def test_output_shape_matches_louvain(self):
        """输出格式与 louvain 一致：0 起始连续簇 ID（reindex）。"""
        from app.services.graph_algorithms.louvain import louvain

        p_l = louvain(_TWO_COMMUNITY)
        p_g = leiden(_TWO_COMMUNITY)
        assert sorted(set(p_l.values())) == list(range(len(set(p_l.values()))))
        assert sorted(set(p_g.values())) == list(range(len(set(p_g.values()))))
        # 同一图上两算法都应把弱桥边两侧拆开
        assert p_g["a"] != p_g["d"]

    def test_deterministic_seed_zero(self):
        """seed=0 固定确定性：两次运行结果一致。"""
        assert leiden(_TWO_COMMUNITY) == leiden(_TWO_COMMUNITY)

    def test_empty_graph(self):
        assert leiden({}) == {}

    def test_single_node(self):
        assert leiden({"s1": {}}) == {"s1": 0}

    def test_resolution_affects_granularity(self):
        """γ 语义与 louvain 一致：>1 细簇 / <1 粗簇（簇数单调）。"""
        n_fine = len(set(leiden(_TWO_COMMUNITY, resolution=2.0).values()))
        n_default = len(set(leiden(_TWO_COMMUNITY, resolution=1.0).values()))
        n_coarse = len(set(leiden(_TWO_COMMUNITY, resolution=0.5).values()))
        assert n_fine >= n_default >= n_coarse

    def test_undirected_edges_deduplicated(self):
        """双向邻接表去重：图构建不重复加边（不抛错且结果稳定）。"""
        graph = {
            "x": {"y": 2.0, "z": 2.0},
            "y": {"x": 2.0},
            "z": {"x": 2.0},
        }
        p1 = leiden(graph)
        p2 = leiden(graph)
        assert p1 == p2
        assert set(p1) == {"x", "y", "z"}

    def test_import_error_when_deps_missing(self):
        """依赖缺失时模块 import 即失败——模拟未安装场景。"""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name in ("igraph", "leidenalg", "igraph.drawing"):
                raise ImportError(f"No module named {name!r}")
            return real_import(name, *args, **kwargs)

        with pytest.raises(ImportError):
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(builtins, "__import__", fake_import)
                # 强制重新导入（清除模块缓存后 import 抛 ImportError）
                import importlib
                import sys

                sys.modules.pop("app.services.graph_algorithms.leiden", None)
                importlib.import_module("app.services.graph_algorithms.leiden")
