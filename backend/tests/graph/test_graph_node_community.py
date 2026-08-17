"""图谱节点 communityId 字段单元测试。

验证 graph.py 各视图查询返回的节点包含契约要求的 communityId。
08-17 P2 迁移后热路径（panorama / view）走 async 驱动（async_neo4j_driver），
fake 同步改 async（async with 会话 + async run + async 结果迭代/data）。
"""

import pytest
from unittest.mock import patch

from app.api.v1 import graph as graph_api


class FakeNode:
    def __init__(self, **props):
        self._props = props

    def get(self, key: str, default=None):
        return self._props.get(key, default)


class _FakeRecord:
    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key: str):
        return self._data[key]

    def get(self, key: str, default=None):
        return self._data.get(key, default)


class _FakeRows:
    """async 结果：async for 产出 _FakeRecord，data() 产出原始 dict 记录。"""

    def __init__(self, records: list[dict]):
        self._records = records

    def __aiter__(self):
        async def _gen():
            for r in self._records:
                yield _FakeRecord(r)

        return _gen()

    async def data(self):
        return list(self._records)


class _FakeSession:
    def __init__(self, records: list[dict]):
        self._records = records

    async def run(self, query, **params):
        return _FakeRows(self._records)


class _FakeSessionCtx:
    """async with driver.session() as session 的上下文管理器。"""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


class _FakeDriver:
    def __init__(self, records: list[dict]):
        self._records = records

    def session(self):
        return _FakeSessionCtx(_FakeSession(self._records))


class TestPanoramaCommunityId:
    @pytest.mark.asyncio
    async def test_panorama_nodes_include_community_id(self):
        records = [
            {
                "p": FakeNode(id="p1", name="Java 后端", status="active", community_id="0"),
                "s": FakeNode(id="s1", name="Spring", community_id="0"),
                "r": FakeNode(weight=0.8, necessity="must", level="中级"),
            },
            {
                "p": FakeNode(id="p2", name="算法工程师", status="active", community_id="1"),
                "s": FakeNode(id="s2", name="PyTorch", community_id="1"),
                "r": FakeNode(weight=0.4, necessity="nice", level="中级"),
            },
        ]
        with patch.object(graph_api, "async_neo4j_driver", _FakeDriver(records)):
            nodes, edges = await graph_api._query_panorama("all", None, 0.3, 10)

        assert len(nodes) == 4
        for n in nodes.values():
            assert "communityId" in n
        assert nodes["p1"]["communityId"] == "0"
        assert nodes["s2"]["communityId"] == "1"


class TestViewMainCommunityId:
    @pytest.mark.asyncio
    async def test_view_main_records_include_community_id(self):
        records = [
            {
                "p": FakeNode(id="p1", name="Java 后端", status="active", community_id="2"),
                "s": FakeNode(id="s1", name="Spring", community_id="2"),
                "r": FakeNode(weight=0.8, necessity="must", level="中级"),
            },
        ]
        with patch.object(graph_api, "async_neo4j_driver", _FakeDriver(records)):
            rows = await graph_api._query_view_main(10, "true")

        assert len(rows) == 1
        row = rows[0]
        assert row["p"].get("community_id") == "2"
        assert row["s"].get("community_id") == "2"


class TestViewTechstackCommunityId:
    @pytest.mark.asyncio
    async def test_view_techstack_query_returns_community_fields(self):
        records = [
            {
                "sid": "s1",
                "sname": "Spring",
                "s_community": "3",
                "pid": "p1",
                "pname": "Java 后端",
                "pstatus": "active",
                "p_community": "3",
                "r": FakeNode(weight=0.8, necessity="must", level="中级"),
            },
        ]
        with patch.object(graph_api, "async_neo4j_driver", _FakeDriver(records)):
            rows = await graph_api._query_view_techstack(10, "true")

        assert len(rows) == 1
        row = rows[0]
        assert row["s_community"] == "3"
        assert row["p_community"] == "3"
