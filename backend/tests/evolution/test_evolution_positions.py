"""岗位演化列表单元测试（GET /evolution/positions，08-15 新增默认岗位展示）。"""

import json
from types import SimpleNamespace

import pytest

from app.api.v1.evolution import _rebuild_position_evolution, position_evolution_list


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
        data = _rebuild_position_evolution(snapshots, "pos_1")
        assert data["position_id"] == "pos_1"
        assert data["position_name"] == "后端工程师"
        assert [p["present"] for p in data["points"]] == [True, True, False]
        assert [p["freq"] for p in data["points"]] == [1, 2, 0]
        assert data["points"][0]["version"] == "graph_v20260801"

    def test_name_falls_back_to_id(self):
        snapshots = [_version("graph_v20260801", [_node("pos_x", "")], [])]
        data = _rebuild_position_evolution(snapshots, "pos_x")
        assert data["position_name"] == "pos_x"


class TestPositionEvolutionList:
    @pytest.mark.asyncio
    async def test_returns_top_positions_by_heat(self):
        snapshots = [
            _version("graph_v20260801", [_node("pos_1", "后端工程师"), _node("pos_2", "前端工程师")], [("pos_1", "s1")]),
            _version("graph_v20260802", [_node("pos_1", "后端工程师")], [("pos_1", "s1"), ("pos_1", "s2")]),
        ]
        db = _db_with(snapshots)
        res = await position_evolution_list(limit=8, db=db, user={})
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
        res = await position_evolution_list(limit=8, db=db, user={})
        positions = res.data["positions"]
        assert [p["position_id"] for p in positions] == ["pos_1"]

    @pytest.mark.asyncio
    async def test_limit_applies(self):
        snapshots = [
            _version(
                "graph_v20260801",
                [_node("pos_1"), _node("pos_2"), _node("pos_3")],
                [],
            ),
        ]
        db = _db_with(snapshots)
        res = await position_evolution_list(limit=2, db=db, user={})
        assert len(res.data["positions"]) == 2

    @pytest.mark.asyncio
    async def test_no_snapshots_returns_404(self):
        db = _db_with([])
        res = await position_evolution_list(limit=8, db=db, user={})
        assert res.status_code == 404
        assert json.loads(res.body)["code"] == 4040
