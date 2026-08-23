"""岗位演化列表单元测试（GET /evolution/positions，08-15 新增默认岗位展示）。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1 import evolution as evolution_api
from app.api.v1.evolution import (
    _build_snapshot_indexes,
    _rebuild_node_evolution,
    _rebuild_position_evolution,
    position_evolution_list,
    skill_evolution_list,
)


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """单测不依赖真实 Redis（缓存层 08-15 新增）：get 恒 None、set 不写。

    本地 redis 若在运行，真实 set 会让缓存跨用例命中（如 limit=2 的结果
    被 limit=8 的 404 用例读到），测试隔离被破坏——按项目惯例换 AsyncMock。
    """
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=None)
    monkeypatch.setattr(evolution_api, "redis_client", redis)


def _version(vid: str, nodes: list[dict], edges: list[tuple[str, str]]):
    """构造 GraphVersion 桩（snapshot_json/created_at/id）。"""
    return SimpleNamespace(
        id=vid,
        created_at=SimpleNamespace(date=lambda: SimpleNamespace(isoformat=lambda: f"2026-08-{vid[-2:]}")),
        snapshot_json={
            "nodes": nodes,
            "edges": [{"source": s, "target": t} for s, t in edges],
        },
    )


def _node(nid: str, name: str = "", type_: str = "position") -> dict:
    return {"id": nid, "name": name or nid, "type": type_}


def _db_with(snapshots: list) -> SimpleNamespace:
    async def scalars(stmt):
        return snapshots  # 真代码 ScalarResult 可迭代（端点内 [v for v in rows]）
    return SimpleNamespace(scalars=scalars)


class TestRebuildPositionEvolution:
    def test_rebuilds_points_across_snapshots(self):
        snapshots = [
            _version("graph_v20260801", [_node("pos_1", "后端工程师")], [("pos_1", "s1")]),
            _version("graph_v20260802", [_node("pos_1", "后端工程师")], [("pos_1", "s1"), ("pos_1", "s2")]),
            _version("graph_v20260803", [], []),  # 岗位消失
        ]
        data = _rebuild_position_evolution(_build_snapshot_indexes(snapshots), "pos_1")
        assert data["position_id"] == "pos_1"
        assert data["position_name"] == "后端工程师"
        assert [p["present"] for p in data["points"]] == [True, True, False]
        assert [p["freq"] for p in data["points"]] == [1, 2, 0]
        assert data["points"][0]["version"] == "graph_v20260801"

    def test_name_falls_back_to_id(self):
        snapshots = [_version("graph_v20260801", [_node("pos_x", "")], [])]
        data = _rebuild_position_evolution(_build_snapshot_indexes(snapshots), "pos_x")
        assert data["position_name"] == "pos_x"


class TestPositionEvolutionList:
    @pytest.mark.asyncio
    async def test_returns_top_positions_by_heat(self):
        snapshots = [
            _version("graph_v20260801", [_node("pos_1", "后端工程师"), _node("pos_2", "前端工程师")], [("pos_1", "s1")]),
            _version("graph_v20260802", [_node("pos_1", "后端工程师")], [("pos_1", "s1"), ("pos_1", "s2")]),
        ]
        db = _db_with(snapshots)
        res = await position_evolution_list(page=1, size=8, db=db, user={})
        positions = res.data["positions"]
        # pos_1 出现 2 期 > pos_2 1 期 → 首位
        assert positions[0]["position_id"] == "pos_1"
        assert positions[0]["position_name"] == "后端工程师"
        assert positions[0]["points"][0]["freq"] == 1
        assert positions[1]["position_id"] == "pos_2"

    @pytest.mark.asyncio
    async def test_filters_non_position_nodes(self):
        snapshots = [
            _version(
                "graph_v20260801",
                [_node("pos_1", "后端工程师"), _node("sk_1", "Python", "skill"), _node("ev_1", "证据", "evidence")],
                [("pos_1", "sk_1")],
            ),
        ]
        db = _db_with(snapshots)
        res = await position_evolution_list(page=1, size=8, db=db, user={})
        positions = res.data["positions"]
        assert [p["position_id"] for p in positions] == ["pos_1"]

    @pytest.mark.asyncio
    async def test_page_size_applies_and_total(self):
        snapshots = [
            _version(
                "graph_v20260801",
                [_node(f"pos_{i}") for i in range(1, 13)],
                [],
            ),
        ]
        db = _db_with(snapshots)
        res = await position_evolution_list(page=1, size=10, db=db, user={})
        assert len(res.data["positions"]) == 10
        assert res.data["total"] == 12
        assert res.data["page"] == 1
        assert res.data["size"] == 10

    @pytest.mark.asyncio
    async def test_page_2_returns_next_slice(self):
        """第 2 页返回第 11 项起（分页切片，08-16）。"""
        snapshots = [
            _version(
                "graph_v20260801",
                [_node(f"pos_{i}") for i in range(1, 13)],
                [],
            ),
        ]
        db = _db_with(snapshots)
        res = await position_evolution_list(page=2, size=10, db=db, user={})
        assert [p["position_id"] for p in res.data["positions"]] == ["pos_11", "pos_12"]
        assert res.data["total"] == 12

    @pytest.mark.asyncio
    async def test_page_beyond_range_returns_empty(self):
        """越界页返回空列表（total 不变）。"""
        snapshots = [
            _version("graph_v20260801", [_node("pos_1")], []),
        ]
        db = _db_with(snapshots)
        res = await position_evolution_list(page=9, size=10, db=db, user={})
        assert res.data["positions"] == []
        assert res.data["total"] == 1

    @pytest.mark.asyncio
    async def test_no_snapshots_returns_404(self):
        db = _db_with([])
        res = await position_evolution_list(page=1, size=8, db=db, user={})
        assert res.status_code == 404
        assert json.loads(res.body)["code"] == 4040

class TestSkillEvolutionList:
    @pytest.mark.asyncio
    async def test_returns_top_skills_by_heat_target_side(self):
        snapshots = [
            _version("graph_v20260801", [_node("sk_1", "Python", "skill"), _node("sk_2", "React", "skill")], [("pos_1", "sk_1")]),
            _version("graph_v20260802", [_node("sk_1", "Python")], [("pos_1", "sk_1"), ("pos_2", "sk_1")]),
        ]
        db = _db_with(snapshots)
        res = await skill_evolution_list(page=1, size=8, db=db, user={})
        skills = res.data["skills"]
        # sk_1 出现 2 期且 freq 更高 → 首位；freq 按 edges.target 统计
        assert skills[0]["skill_id"] == "sk_1"
        assert skills[0]["skill_name"] == "Python"
        assert skills[0]["points"][1]["freq"] == 2
        assert skills[1]["skill_id"] == "sk_2"
        assert skills[1]["points"][0]["freq"] == 0  # target 侧无引用

    @pytest.mark.asyncio
    async def test_filters_non_skill_nodes(self):
        snapshots = [
            _version(
                "graph_v20260801",
                [_node("sk_1", "Python", "skill"), _node("pos_1", "后端工程师"), _node("co_1", "课程", "course")],
                [("pos_1", "sk_1")],
            ),
        ]
        db = _db_with(snapshots)
        res = await skill_evolution_list(page=1, size=8, db=db, user={})
        assert [s["skill_id"] for s in res.data["skills"]] == ["sk_1"]

    @pytest.mark.asyncio
    async def test_limit_and_no_snapshots(self):
        snapshots = [_version("graph_v20260801", [_node("sk_1"), _node("sk_2"), _node("sk_3")], [])]
        db = _db_with(snapshots)
        res = await skill_evolution_list(page=1, size=2, db=db, user={})
        assert len(res.data["skills"]) == 2
        db0 = _db_with([])
        res0 = await skill_evolution_list(page=1, size=8, db=db0, user={})
        assert res0.status_code == 404


class TestRebuildNodeEvolution:
    def test_edge_side_target(self):
        snapshots = [
            _version("graph_v20260801", [_node("sk_1", "Python", "skill")], [("pos_1", "sk_1")]),
            _version("graph_v20260802", [], []),
        ]
        name, points = _rebuild_node_evolution(_build_snapshot_indexes(snapshots), "sk_1", edge_side="target")
        assert name == "Python"
        assert [p["freq"] for p in points] == [1, 0]
        assert [p["present"] for p in points] == [True, False]
