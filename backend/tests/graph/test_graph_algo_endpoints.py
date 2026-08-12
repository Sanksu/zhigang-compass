"""图算法端点单元测试（图算法优化方案阶段一 + 契约对齐）。

覆盖：
- skill-clusters：resolution 参数传递到 louvain、缓存键含 resolution
  （防新旧参数串缓存）、响应含 label/needs_llm/triggers/llm 字段
- pagerank：min_weight 默认值 2.0（与 skill-clusters 取数口径统一）、
  排名响应结构

mock 策略：patch 端点函数内 import 的 louvain/load_skill_cooccurrence
（函数体内 import 在调用时解析模块属性，monkeypatch 模块属性即生效），
绕过真实 Neo4j；redis_client/neo4j_driver 全局换 MagicMock。
"""

import asyncio
import inspect
import json
from unittest.mock import AsyncMock, MagicMock

from app.api.v1 import graph as graph_api


def _call(coro):
    """项目约定：async 测试显式 asyncio.run 包裹（不依赖 pytest-asyncio）。"""
    return asyncio.run(coro)


def _patch_network_and_louvain(monkeypatch, graph, name_map, louvain_impl):
    """patch 共现网络与聚类实现，返回记录调用参数的容器。"""
    calls = {"clustering": []}

    def fake_load(session, min_weight=2.0):
        calls["min_weight"] = min_weight
        return graph, name_map

    def fake_hierarchical(g, resolution=1.0):
        calls["clustering"].append({"graph": g, "resolution": resolution})
        return {
            "levels": [
                {"level": 0, "membership": louvain_impl, "modularity": 0.1, "cluster_count": len(set(louvain_impl.values())) or 0},
            ],
            "best_level": 0,
            "membership": louvain_impl,
        }

    monkeypatch.setattr("app.services.graph_algorithms.network.load_skill_cooccurrence", fake_load)
    monkeypatch.setattr("app.services.graph_algorithms.louvain.louvain_hierarchical", fake_hierarchical)
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)  # redis_client.get/set 均为 async
    redis.set = AsyncMock(return_value=None)
    monkeypatch.setattr(graph_api, "redis_client", redis)
    monkeypatch.setattr(graph_api, "neo4j_driver", MagicMock())
    return calls


class TestSkillClusters:
    def test_resolution_passed_to_louvain(self, monkeypatch):
        """resolution 参数透传到 louvain_hierarchical。"""
        calls = _patch_network_and_louvain(
            monkeypatch,
            graph={"s1": {"s2": 1.0}, "s2": {"s1": 1.0}},
            name_map={"s1": "Python", "s2": "Django"},
            louvain_impl={"s1": 0, "s2": 0},
        )
        resp = _call(graph_api.graph_skill_clusters(min_size=2, resolution=1.5))
        assert resp.code == 0
        assert calls["clustering"][0]["resolution"] == 1.5

    def test_level_passed_to_clustering(self, monkeypatch):
        """level 参数透传；levels 元数据随响应返回（阶段三层次化提取）。"""
        calls = _patch_network_and_louvain(
            monkeypatch,
            graph={"s1": {"s2": 1.0}, "s2": {"s1": 1.0}},
            name_map={"s1": "Python", "s2": "Django"},
            louvain_impl={"s1": 0, "s2": 0},
        )
        resp = _call(graph_api.graph_skill_clusters(min_size=2, resolution=1.0, level=2))
        assert resp.code == 0
        assert calls["clustering"][0]["resolution"] == 1.0
        # 层级元数据（fake 返回 1 层；modularity 为标准 Q 口径——单簇划分 Q=0）
        assert resp.data["levels"] == [
            {"level": 0, "cluster_count": 1, "modularity": 0.0},
        ]

    def test_cache_key_contains_resolution(self, monkeypatch):
        """缓存键含 algorithm + resolution + level：参数不同不串缓存。"""
        _patch_network_and_louvain(
            monkeypatch,
            graph={"s1": {"s2": 1.0}, "s2": {"s1": 1.0}},
            name_map={"s1": "Python", "s2": "Django"},
            louvain_impl={"s1": 0, "s2": 0},
        )
        monkeypatch.setattr(
            graph_api, "load_graph_algo_config",  # graph.py 顶部绑定（from-import 后 patch 此处生效）
            lambda: {"algorithm": "louvain", "resolution": 1.0, "min_weight": 2.0, "min_size": 2},
        )
        _call(graph_api.graph_skill_clusters(min_size=2, resolution=1.0, level=None))
        _call(graph_api.graph_skill_clusters(min_size=2, resolution=1.5, level=None))
        _call(graph_api.graph_skill_clusters(min_size=2, resolution=1.0, level=1))

        keys = [c.args[0] for c in graph_api.redis_client.set.call_args_list]
        assert keys == [
            "graph:algo:clusters:louvain:2:1.0:None",
            "graph:algo:clusters:louvain:2:1.5:None",
            "graph:algo:clusters:louvain:2:1.0:1",
        ]

    def test_algorithm_leiden_uses_leiden(self, monkeypatch):
        """algorithm=leiden 时调用 leiden（同签名），缓存键含 leiden。"""
        _patch_network_and_louvain(
            monkeypatch,
            graph={"s1": {"s2": 1.0}, "s2": {"s1": 1.0}},
            name_map={"s1": "Python", "s2": "Django"},
            louvain_impl={"s1": 0, "s2": 0},
        )
        calls = {"leiden": []}

        def fake_leiden(g, resolution=1.0):
            calls["leiden"].append({"graph": g, "resolution": resolution})
            return {"s1": 0, "s2": 0}

        monkeypatch.setattr("app.services.graph_algorithms.leiden.leiden", fake_leiden)
        monkeypatch.setattr(
            graph_api, "load_graph_algo_config",  # graph.py 顶部绑定（from-import 后 patch 此处生效）
            lambda: {"algorithm": "leiden", "resolution": 1.0, "min_weight": 2.0, "min_size": 2},
        )
        resp = _call(graph_api.graph_skill_clusters(min_size=2, resolution=1.0))
        assert resp.code == 0
        assert len(calls["leiden"]) == 1
        assert calls["leiden"][0]["resolution"] == 1.0
        assert "leiden" in graph_api.redis_client.set.call_args.args[0]

    def test_leiden_import_error_falls_back_to_louvain(self, monkeypatch):
        """leiden 依赖缺失（ImportError）自动回退 louvain_hierarchical 并告警，不阻塞 API。"""
        calls = _patch_network_and_louvain(
            monkeypatch,
            graph={"s1": {"s2": 1.0}, "s2": {"s1": 1.0}},
            name_map={"s1": "Python", "s2": "Django"},
            louvain_impl={"s1": 0, "s2": 0},
        )
        monkeypatch.setattr(
            graph_api, "load_graph_algo_config",  # graph.py 顶部绑定（from-import 后 patch 此处生效）
            lambda: {"algorithm": "leiden", "resolution": 1.0, "min_weight": 2.0, "min_size": 2},
        )
        def boom(g, resolution=1.0):
            raise ImportError("igraph 未安装")

        monkeypatch.setattr("app.services.graph_algorithms.leiden.leiden", boom)
        resp = _call(graph_api.graph_skill_clusters(min_size=2, resolution=1.0))
        assert resp.code == 0  # 回退 louvain 正常响应
        assert len(calls["clustering"]) == 1  # louvain_hierarchical 被调用（回退路径）

    def test_response_includes_label_and_llm_fields(self, monkeypatch):
        """响应契约：clusters 项含 label/needs_llm/triggers/llm（契约对齐缺口）。"""
        _patch_network_and_louvain(
            monkeypatch,
            graph={"s1": {"s2": 1.0}, "s2": {"s1": 1.0}},
            name_map={"s1": "Python", "s2": "Django"},
            louvain_impl={"s1": 0, "s2": 0},
        )
        resp = _call(graph_api.graph_skill_clusters(min_size=2, resolution=1.0))
        assert resp.code == 0
        item = resp.data["clusters"][0]
        assert item["label"].startswith("Python")
        assert "needs_llm" in item
        assert "triggers" in item
        assert "llm" in item  # 未配置 LLM 时为 None（降级），字段必须存在

    def test_min_size_filters_clusters(self, monkeypatch):
        """min_size 过滤过小簇。"""
        _patch_network_and_louvain(
            monkeypatch,
            graph={"s1": {"s2": 1.0}, "s2": {"s1": 1.0}},
            name_map={"s1": "Python", "s2": "Django"},
            louvain_impl={"s1": 0, "s2": 0},
        )
        resp = _call(graph_api.graph_skill_clusters(min_size=3, resolution=1.0))
        assert resp.data["clusters"] == []
        assert resp.data["cluster_count"] == 0

    def test_resolution_bounds_rejected(self, monkeypatch):
        """resolution 越界（<0.1 或 >5.0）由 Query 约束拒绝——签名校验。"""
        from annotated_types import Ge, Le

        params = inspect.signature(graph_api.graph_skill_clusters).parameters
        res_q = params["resolution"].default
        ge_cons = [m for m in res_q.metadata if isinstance(m, Ge)]
        le_cons = [m for m in res_q.metadata if isinstance(m, Le)]
        assert ge_cons and ge_cons[0].ge == 0.1
        assert le_cons and le_cons[0].le == 5.0


class TestPageRank:
    def test_default_params_follow_config(self):
        """pagerank/skill-clusters 默认参数随 configs/graph_algo.yaml（调优值接入 API）。"""
        from app.services.graph_algorithms.config import load_graph_algo_config

        cfg = load_graph_algo_config()
        pr = inspect.signature(graph_api.graph_pagerank).parameters
        assert pr["min_weight"].default.default == cfg["min_weight"]
        assert pr["top_n"].default.default == 20
        sc = inspect.signature(graph_api.graph_skill_clusters).parameters
        assert sc["resolution"].default.default == cfg["resolution"]

    def test_min_weight_forwarded_to_network(self, monkeypatch):
        """min_weight 透传到共现网络加载。"""
        calls = _patch_network_and_louvain(
            monkeypatch,
            graph={"s1": {"s2": 2.0}, "s2": {"s1": 2.0}},
            name_map={"s1": "Python", "s2": "Django"},
            louvain_impl={},
        )
        resp = _call(graph_api.graph_pagerank(top_n=2, min_weight=2.5))
        assert resp.code == 0
        assert calls["min_weight"] == 2.5

    def test_returns_ranked_skills(self, monkeypatch):
        """排名响应结构：skills 按 score 降序、含 top_n。"""
        graph = {
            "hub": {"a": 1.0, "b": 1.0},
            "a": {"hub": 1.0},
            "b": {"hub": 1.0},
        }
        _patch_network_and_louvain(
            monkeypatch,
            graph=graph,
            name_map={"hub": "中心", "a": "A", "b": "B"},
            louvain_impl={},
        )
        resp = _call(graph_api.graph_pagerank(top_n=2))
        assert resp.data["top_n"] == 2
        scores = [s["score"] for s in resp.data["skills"]]
        assert scores == sorted(scores, reverse=True)
        # 中心节点应排第一（星型图）
        assert resp.data["skills"][0]["name"] == "中心"
        assert "graph:algo:pagerank" in graph_api.redis_client.set.call_args.args[0]

    def test_cache_hit_skips_network(self, monkeypatch):
        """缓存命中时不查 Neo4j（load_skill_cooccurrence 不被调用）。"""
        calls = _patch_network_and_louvain(
            monkeypatch,
            graph={},
            name_map={},
            louvain_impl={},
        )
        cached = json.dumps({"skills": [], "top_n": 0})
        graph_api.redis_client.get.return_value = cached
        resp = _call(graph_api.graph_pagerank(top_n=5))
        assert resp.data == {"skills": [], "top_n": 0}
        # 缓存命中路径不进入 _compute，load 的 min_weight 记录保持 None
        assert "min_weight" not in calls or calls.get("min_weight") is None


class TestCommunityTree:
    """阶段三：community-tree 端点（Neo4j Community 节点 → 树结构）。"""

    def _patch_neo4j_tree(self, monkeypatch, node_rows, edge_rows):
        session = MagicMock()
        session.__enter__.return_value = session  # with session as s 绑定同一实例
        session.run.side_effect = [node_rows, edge_rows]
        driver = MagicMock()
        driver.session.return_value = session
        monkeypatch.setattr(graph_api, "neo4j_driver", driver)
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock(return_value=None)
        monkeypatch.setattr(graph_api, "redis_client", redis)

    def test_builds_tree_with_nested_children(self, monkeypatch):
        """NESTED_IN 边组装树：根 = 无父的最高层社区，children 递归展开。"""
        node_rows = [
            {"cid": "comm_0_0", "level": 0, "name": "Python·Django", "cluster_count": 2, "modularity": 0.1, "top_skills": ["Python", "Django"]},
            {"cid": "comm_0_1", "level": 0, "name": "K8s·Docker", "cluster_count": 2, "modularity": 0.1, "top_skills": ["Kubernetes"]},
            {"cid": "comm_1_0", "level": 1, "name": "Python·Django·K8s", "cluster_count": 1, "modularity": 0.25, "top_skills": ["Python"]},
        ]
        edge_rows = [
            {"child": "comm_0_0", "parent": "comm_1_0"},
            {"child": "comm_0_1", "parent": "comm_1_0"},
        ]
        self._patch_neo4j_tree(monkeypatch, node_rows, edge_rows)
        resp = _call(graph_api.graph_community_tree())
        assert resp.code == 0
        assert resp.data["levels"] == [0, 1]
        assert len(resp.data["tree"]) == 1
        root = resp.data["tree"][0]
        assert root["id"] == "comm_1_0"
        assert root["name"] == "Python·Django·K8s"
        assert sorted(c["id"] for c in root["children"]) == ["comm_0_0", "comm_0_1"]

    def test_empty_tree_when_not_synced(self, monkeypatch):
        """未同步（无 Community 节点）返回空树（前端提示先运行 sync 脚本）。"""
        self._patch_neo4j_tree(monkeypatch, node_rows=[], edge_rows=[])
        resp = _call(graph_api.graph_community_tree())
        assert resp.code == 0
        assert resp.data == {"tree": [], "levels": []}

    def test_cache_key_stable(self, monkeypatch):
        """community-tree 使用固定缓存键（30s TTL）。"""
        self._patch_neo4j_tree(monkeypatch, node_rows=[], edge_rows=[])
        _call(graph_api.graph_community_tree())
        key = graph_api.redis_client.set.call_args.args[0]
        assert key == "graph:algo:community-tree"
