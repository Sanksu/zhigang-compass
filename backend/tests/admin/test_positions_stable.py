"""已晋级 stable 岗位全集端点测试（GET /admin/positions/stable）。

背景：/positions/pending?state=stable 仅返回候选池行；本端点把图谱
Position.status='stable' 且候选池无同名行的"留存节点"并入（按岗位名去重、
候选池优先、source 标记来源），图谱不可达时降级为候选池子集。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.api.v1.admin_routes.position_reviews import positions_stable


def _make_pool(name: str, conf: float | None = 0.8) -> SimpleNamespace:
    return SimpleNamespace(
        position_name=name,
        state="stable",
        confidence={"final_confidence": conf} if conf is not None else {},
        seed_matched=False,
        rag_matched=True,
        detected_at="2026-08-01T00:00:00",
    )


class _Rows:
    """scalars() 返回的可 .all() 包装（对齐 SQLAlchemy AsyncResult）。"""

    def __init__(self, rows: list):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, pool_rows: list):
        self._pool_rows = pool_rows

    async def scalars(self, stmt):
        return _Rows(self._pool_rows)


def _run(db, graph_nodes=None, graph_error: Exception | None = None):
    async def run():
        patch_target = "app.services.graph.repository.query_stable_positions_async"
        if graph_error is not None:
            patcher = patch(
                patch_target, new=AsyncMock(side_effect=graph_error),
            )
        else:
            patcher = patch(patch_target, new=AsyncMock(return_value=graph_nodes or []))
        with patcher:
            return await positions_stable(db=db)

    return asyncio.run(run())


class TestPositionsStable:
    def test_union_pool_plus_graph_only_dedup(self):
        """候选池 stable ∪ 图谱 stable：按名去重，候选池优先，图谱独有节点 source=graph。"""
        db = _FakeDB([_make_pool("岗位A"), _make_pool("岗位B")])
        graph_nodes = [
            {"name": "岗位A", "state_updated_at": "2026-08-02T00:00:00", "freq": 12},
            {"name": "岗位B", "state_updated_at": "2026-08-02T00:00:00", "freq": 8},
            {"name": "岗位C", "state_updated_at": "2026-08-03T00:00:00", "freq": 5},
        ]
        resp = _run(db, graph_nodes)

        items = resp.data["items"]
        assert resp.data["total"] == 3
        by_name = {it["position_name"]: it for it in items}
        # 候选池行优先（完整画像），图谱同名节点被去重
        assert by_name["岗位A"]["source"] == "pool"
        assert by_name["岗位A"]["confidence"] == {"final_confidence": 0.8}
        assert by_name["岗位A"]["state_updated_at"] is None
        # 图谱独有节点：source=graph，无候选池画像字段
        assert by_name["岗位C"]["source"] == "graph"
        assert by_name["岗位C"]["confidence"] is None
        assert by_name["岗位C"]["detected_at"] is None
        assert by_name["岗位C"]["state_updated_at"] == "2026-08-03T00:00:00"
        assert by_name["岗位C"]["freq"] == 5

    def test_graph_unreachable_degrade_to_pool(self):
        """图谱不可达：降级为候选池子集，不抛错。"""
        db = _FakeDB([_make_pool("岗位A")])
        resp = _run(db, graph_error=RuntimeError("Neo4j unavailable"))

        assert resp.data["total"] == 1
        assert resp.data["items"][0]["position_name"] == "岗位A"
        assert resp.data["items"][0]["source"] == "pool"

    def test_empty_pool_with_graph_nodes(self):
        """候选池无 stable 行但图谱有节点：全部以 source=graph 返回。"""
        db = _FakeDB([])
        graph_nodes = [
            {"name": "图谱岗1", "state_updated_at": None, "freq": 0},
            {"name": "图谱岗2", "state_updated_at": "2026-08-04T00:00:00", "freq": 3},
        ]
        resp = _run(db, graph_nodes)

        assert resp.data["total"] == 2
        sources = {it["position_name"]: it["source"] for it in resp.data["items"]}
        assert sources == {"图谱岗1": "graph", "图谱岗2": "graph"}

    def test_empty_both(self):
        """两域皆空：空 items，total=0。"""
        resp = _run(_FakeDB([]))
        assert resp.data["total"] == 0
        assert resp.data["items"] == []
