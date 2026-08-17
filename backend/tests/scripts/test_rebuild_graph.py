"""rebuild_graph 审核状态回写测试（08-16 审查：审核列表 ↔ 图谱对应，风险 A）。

图谱重建会把全部岗位置为 active，已审核状态（emerging/stable/declining/
archived/rejected）存在 discovery_candidates——本测试验证回写只针对已审核
状态、只更新图谱已存在节点（MATCH）、不创建孤儿节点。
"""

import pytest

from scripts.rebuild_graph import _REVIEWED_STATES, _restore_reviewed_statuses


class _Row:
    def __init__(self, name: str, state: str):
        self.position_name = name
        self.state = state


class _Result:
    """模拟 AsyncScalarResult：.all() 返回预置行。"""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakePGSession:
    """模拟 async_session_factory 会话：scalars 返回预置行。"""

    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalars(self, stmt):
        return _Result(self._rows)


class _FakeNeo4j:
    """模拟 neo4j_driver：记录每次 run 的语句与参数。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query: str, **params):
        self.calls.append((query, params))


@pytest.mark.asyncio
async def test_restores_only_reviewed_statuses(monkeypatch):
    """回写全部已审核状态岗位（SQL 层已过滤 candidate，见 _REVIEWED_STATES 常量测试）。"""
    rows = [
        _Row("算法工程师", "emerging"),
        _Row("鸿蒙开发工程师", "archived"),
        _Row("首席统计师", "rejected"),
    ]

    # async_sessionmaker() 同步返回 AsyncSession（async with 消费），fake 同构
    def _factory():
        return _FakePGSession(rows)

    neo4j = _FakeNeo4j()
    monkeypatch.setattr("scripts.rebuild_graph.async_session_factory", _factory)
    monkeypatch.setattr("scripts.rebuild_graph.neo4j_driver", neo4j)

    count = await _restore_reviewed_statuses()

    assert count == 3
    assert len(neo4j.calls) == 3
    assert {c[1]["state"] for c in neo4j.calls} == {"emerging", "archived", "rejected"}
    for query, params in neo4j.calls:
        # MATCH 而非 MERGE：不创建孤儿节点（无 JD 支撑的已审核岗位不复活）
        assert "MATCH (p:Position {name: $name})" in query
        assert "SET p.status = $state" in query
        assert "state_updated_at" in query
        assert params["name"] and params["state"] and params["now"]


@pytest.mark.asyncio
async def test_no_reviewed_rows_skips_neo4j(monkeypatch):
    """候选池无已审核岗位（SQL 过滤后为空）时不触发 Neo4j 写入。"""
    rows: list[_Row] = []

    # async_sessionmaker() 同步返回 AsyncSession（async with 消费），fake 同构
    def _factory():
        return _FakePGSession(rows)

    neo4j = _FakeNeo4j()
    monkeypatch.setattr("scripts.rebuild_graph.async_session_factory", _factory)
    monkeypatch.setattr("scripts.rebuild_graph.neo4j_driver", neo4j)

    count = await _restore_reviewed_statuses()

    assert count == 0
    assert neo4j.calls == []


def test_reviewed_states_exclude_candidate_and_active():
    """回写集合 = 六状态机中除 candidate/active 外的全部状态（发现流程外常态不入池）。"""
    assert set(_REVIEWED_STATES) == {"emerging", "stable", "declining", "archived", "rejected"}
    assert "candidate" not in _REVIEWED_STATES
    assert "active" not in _REVIEWED_STATES
