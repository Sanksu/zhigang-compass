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
    calls = {"louvain": []}

    def fake_load(session, min_weight=2.0):
        calls["min_weight"] = min_weight
        return graph, name_map

    def fake_louvain(g, resolution=1.0):
        calls["louvain"].append({"graph": g, "resolution": resolution})
        return louvain_impl

    monkeypatch.setattr("app.services.graph_algorithms.network.load_skill_cooccurrence", fake_load)
    monkeypatch.setattr("app.services.graph_algorithms.louvain.louvain", fake_louvain)
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)  # redis_client.get/set 均为 async
    redis.set = AsyncMock(return_value=None)
    monkeypatch.setattr(graph_api, "redis_client", redis)
    monkeypatch.setattr(graph_api, "neo4j_driver", MagicMock())
    return calls


class TestSkillClusters:
    def test_resolution_passed_to_louvain(self, monkeypatch):
        """resolution 参数透传到 louvain(graph, resolution)。"""
        calls = _patch_network_and_louvain(
            monkeypatch,
            graph={"s1": {"s2": 1.0}, "s2": {"s1": 1.0}},
            name_map={"s1": "Python", "s2": "Django"},
            louvain_impl={"s1": 0, "s2": 0},
        )
        resp = _call(graph_api.graph_skill_clusters(min_size=2, resolution=1.5))
        assert resp.code == 0
        assert calls["louvain"][0]["resolution"] == 1.5

    def test_cache_key_contains_resolution(self, monkeypatch):
        """缓存键含 resolution：γ 不同不串缓存。"""
        _patch_network_and_louvain(
            monkeypatch,
            graph={"s1": {"s2": 1.0}, "s2": {"s1": 1.0}},
            name_map={"s1": "Python", "s2": "Django"},
            louvain_impl={"s1": 0, "s2": 0},
        )
        _call(graph_api.graph_skill_clusters(min_size=2, resolution=1.0))
        _call(graph_api.graph_skill_clusters(min_size=2, resolution=1.5))

        keys = [c.args[0] for c in graph_api.redis_client.set.call_args_list]
        assert keys == ["graph:algo:clusters:2:1.0", "graph:algo:clusters:2:1.5"]

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
    def test_default_min_weight_aligned_to_2(self):
        """pagerank 默认 min_weight=2.0（与 skill-clusters 取数口径统一）。"""
        params = inspect.signature(graph_api.graph_pagerank).parameters
        assert params["min_weight"].default.default == 2.0
        assert params["top_n"].default.default == 20

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
