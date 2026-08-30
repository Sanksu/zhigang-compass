"""岗位画像视图路由测试（/graph/view/positionPortrait）。

不触真实 Neo4j/Redis：直调 graph_view 端点函数（对齐 test_discovery_api.py
的直调模式），monkeypatch repository 查询、_query_graph_counts 与
redis_client（get 默认未命中，set 异步 no-op）。

fake Record/Node 需同时支持 record["p"] 与 record.get("skills")、
p.get(...) 与 p["required_years"] 两种取值（对齐 neo4j Record/Node 行为），
技能列表元素直接用 dict（消费侧只走 .get/[]）。
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1 import graph as graph_api


class _FakeNode:
    """Neo4j Node stub：graph.py 对画像节点同时用 p.get(...) 与 p[key]。"""

    def __init__(self, props):
        self._props = props

    def __getitem__(self, key):
        return self._props[key]

    def keys(self):
        return self._props.keys()

    def get(self, key, default=None):
        return self._props.get(key, default)


class _FakeRecord:
    """Neo4j Record stub：record["p"] + record.get("skills")。"""

    def __init__(self, **items):
        self._items = items

    def __getitem__(self, key):
        return self._items[key]

    def get(self, key, default=None):
        return self._items.get(key, default)


def _portrait_record():
    """一条画像查询结果：岗位本体（含画像属性）+ 2 条有效技能 + 1 条空 sid。"""
    p = _FakeNode({
        "id": "pos_1",
        "name": "后端工程师",
        "status": "stable",
        "evidence_count": 12,
        "freq": 5,
        "salary_range": "20k-35k",
        "required_years": 3,
        "required_education": "本科",
        "soft_skills": ["沟通能力", "团队协作"],
        "salary_tiers": [{"text": "1-1.3万", "count": 9}],
        "experience_distribution": {"3年以上": 5},
        "education_distribution": {"本科": 8},
    })
    skills = [
        {"sid": "sk_py", "sname": "Python", "scat": "语言",
         "weight": 0.9, "necessity": "must", "level": "高级"},
        {"sid": "sk_k8s", "sname": "Kubernetes", "scat": "运维",
         "weight": 0.5, "necessity": "nice", "level": "中级"},
        {"sid": "", "sname": "脏数据", "scat": None,
         "weight": 0.1, "necessity": "must", "level": None},
    ]
    return _FakeRecord(p=p, skills=skills)


def _patch_env(monkeypatch, rows):
    """mock redis / repository 画像查询 / 图谱计数，返回调用参数容器。"""
    calls = {}

    async def fake_portrait_query(driver, position_id, limit, status_filter):
        calls["query"] = {
            "driver": driver, "position": position_id,
            "limit": limit, "status_filter": status_filter,
        }
        return rows

    async def fake_counts():
        return {"total_nodes": 111, "total_edges": 222}

    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)  # redis_client.get/set 均为 async
    redis.set = AsyncMock(return_value=None)

    monkeypatch.setattr(
        graph_api.repository, "query_view_position_portrait_async",
        fake_portrait_query)
    monkeypatch.setattr(graph_api, "_query_graph_counts", fake_counts)
    monkeypatch.setattr(graph_api, "redis_client", redis)
    monkeypatch.setattr(graph_api, "async_neo4j_driver", SimpleNamespace())
    return calls


class TestPositionPortraitView:
    @pytest.mark.asyncio
    async def test_missing_position_returns_validation_error(self, monkeypatch):
        """缺 position 参数：ERR_VALIDATION（4000）→ HTTP 422。"""
        _patch_env(monkeypatch, rows=[])
        resp = await graph_api.graph_view(
            view_type="positionPortrait", position=None, limit=100, user=None)
        assert resp.status_code == 422
        body = json.loads(resp.body)
        assert body["code"] == 4000
        assert body["data"] is None

    @pytest.mark.asyncio
    async def test_position_not_found_404(self, monkeypatch):
        """岗位不存在/不可见（查询空行）：404 + ERR_NOT_FOUND（4040）。"""
        _patch_env(monkeypatch, rows=[])
        resp = await graph_api.graph_view(
            view_type="positionPortrait", position="ghost", limit=100, user=None)
        assert resp.status_code == 404
        body = json.loads(resp.body)
        assert body["code"] == 4040

    @pytest.mark.asyncio
    async def test_normal_path_hierarchy_nodes_edges_stats(self, monkeypatch):
        """正常路径：中心岗位 + 大类节点（技能/软技能/薪资/经验/学历）+ 各自小节点。"""
        calls = _patch_env(monkeypatch, rows=[_portrait_record()])
        resp = await graph_api.graph_view(
            view_type="positionPortrait", position="pos_1", limit=100,
            user={"role": "guest", "sub": "u1"})

        assert resp.code == 0
        data = resp.data
        assert data["view_type"] == "positionPortrait"

        nodes = {n["id"]: n for n in data["nodes"]}
        edges = data["edges"]

        # 仓库查询透传：驱动 + position + limit + guest 仅公开态过滤
        assert calls["query"]["position"] == "pos_1"
        assert calls["query"]["limit"] == 100
        assert "p.status IN $public_statuses" in calls["query"]["status_filter"]

        # 中心岗位节点
        pos = nodes["pos_1"]
        assert pos["type"] == "position"
        assert pos["name"] == "后端工程师"
        assert pos["evidence_count"] == 12

        # 大类节点（type=attr，5 个维度齐全）+ 岗位→大类边
        for label in ("技能", "软技能", "薪资", "经验", "学历"):
            cat = nodes[f"attr_pos_1_{label}"]
            assert cat["type"] == "attr" and cat["name"] == label
            assert any(e["source"] == "pos_1" and e["target"] == f"attr_pos_1_{label}"
                       for e in edges)

        # 技能小节点挂技能大类下（空 sid 脏行被过滤）
        assert nodes["sk_py"]["type"] == "skill"
        assert nodes["sk_py"]["name"] == "Python"
        assert any(e["source"] == "attr_pos_1_技能" and e["target"] == "sk_py"
                   for e in edges)
        assert all(n["name"] != "脏数据" for n in data["nodes"])

        # 软技能小节点（type=skill + 软技能类目 → 前端粉色）
        assert nodes["soft_pos_1_0"]["type"] == "skill"
        assert nodes["soft_pos_1_0"]["skill_category"] == "软技能"
        assert nodes["soft_pos_1_0"]["name"] == "沟通能力"
        assert any(e["source"] == "attr_pos_1_软技能" and e["target"] == "soft_pos_1_0"
                   for e in edges)

        # 薪资档位/经验/学历小节点（'档位 ×计数' 形态）
        assert nodes["sal_pos_1_0"]["name"] == "1-1.3万 ×9"
        assert nodes["exp_pos_1_0"]["name"] == "3年以上 ×5"
        assert nodes["edu_pos_1_0"]["name"] == "本科 ×8"

        # 边：5 条岗位→大类 + 小节点边（2 技能 + 2 软技能 + 1 薪资 + 1 经验 + 1 学历）
        cat_edges = [e for e in edges if e["source"] == "pos_1"]
        assert len(cat_edges) == 5
        assert data["stats"]["nodes"] == 1 + 5 + 2 + 2 + 1 + 1 + 1
        assert data["stats"]["edges"] == len(edges)

        # 缓存写入：键含岗位（画像缓存按岗位隔离）
        cache_key = graph_api.redis_client.set.call_args.args[0]
        assert "positionPortrait" in cache_key and "pos_1" in cache_key

    @pytest.mark.asyncio
    async def test_salary_fallback_from_min_max(self, monkeypatch):
        """无 salary_range 文本时由 salary_min/max/currency 拼接薪资档。"""
        p = _FakeNode({
            "id": "pos_2", "name": "算法工程师", "status": "emerging",
            "evidence_count": 3, "freq": 2,
            "salary_min": 20000, "salary_max": 35000, "salary_currency": "CNY",
            "required_years": None, "required_education": None,
        })
        _patch_env(monkeypatch, rows=[_FakeRecord(p=p, skills=[])])

        resp = await graph_api.graph_view(
            view_type="positionPortrait", position="pos_2", limit=100, user=None)

        nodes = {n["id"]: n for n in resp.data["nodes"]}
        # 薪资大类单条兜底子节点（min/max 拼接）；经验/学历/技能/软技能为空不生成大类
        assert nodes["sal_pos_2_fallback"]["name"] == "20000-35000元"
        cat_nodes = [n for n in resp.data["nodes"]
                     if n.get("type") == "attr" and n["id"].startswith("attr_pos_2_")]
        assert [n["name"] for n in cat_nodes] == ["薪资"]
        assert resp.data["stats"]["edges"] == 2  # 岗位→薪资 + 薪资→兜底条目
