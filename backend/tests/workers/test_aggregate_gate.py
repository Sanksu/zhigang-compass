"""aggregate_positions 入图门控消费测试（§4.5，H5 闭环）。

验证任务层：新兴岗位名集合查询 → filter_rows_for_aggregation 过滤 →
build_aggregates 只吃放行 rows → 门控统计并入返回值。
数据库/Neo4j 全 stub，对齐 test_discovery_api 的直调模式。
"""

import asyncio
import contextlib
from contextlib import asynccontextmanager
from types import SimpleNamespace

from app.workers import etl_tasks


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class _FakeSession:
    """scalars(stmt) 依次返回预置结果集（第一次 JD 行，第二次新兴岗位名）。"""

    def __init__(self, jd_rows, emerging_names):
        self._results = [jd_rows, emerging_names]

    async def scalars(self, _stmt):
        return _FakeScalars(self._results.pop(0))


def _jd_row(confidence, position_name):
    snap = {"extraction": {"requirements": []}}
    if confidence is not None:
        snap["cross_validation"] = {"confidence": confidence, "position_name": position_name}
    return SimpleNamespace(snapshot=snap, source="boss", crawled_at=None)


def _run(monkeypatch, jd_rows, emerging_names):
    """stub 会话工厂/Neo4j/聚合写回后直调 aggregate_positions。"""
    session = _FakeSession(jd_rows, emerging_names)

    @asynccontextmanager
    async def _fake_factory():
        yield session

    captured = {}

    def _fake_build(rows):
        captured["rows"] = list(rows)
        return {}

    def _fake_write(_session, _agg, _now):
        captured["write"] = True
        return {"positions": 1, "edges": 2, "removed_edges": 0}

    import app.core.database as database

    monkeypatch.setattr(database, "async_session_factory", _fake_factory)
    # write_aggregates 经 `with neo4j_driver.session() as session` 使用驱动
    monkeypatch.setattr(
        database, "neo4j_driver",
        SimpleNamespace(session=lambda: contextlib.nullcontext()),
    )
    import app.services.kg.aggregation as aggregation

    monkeypatch.setattr(aggregation, "build_aggregates", _fake_build)
    monkeypatch.setattr(aggregation, "write_aggregates", _fake_write)
    result = asyncio.run(etl_tasks.aggregate_positions({}))
    return result, captured


def test_low_confidence_jds_never_reach_build(monkeypatch):
    """单源低置信 JD 被门控拦截，不进入 build_aggregates；统计并入返回值。"""
    rows = [
        _jd_row(0.787, "Java开发工程师"),   # 多源高置信 → 放行
        _jd_row(0.333, "小众岗位"),          # 单源低置信 → 拦截
    ]
    result, captured = _run(monkeypatch, rows, set())
    assert [r.snapshot["cross_validation"]["position_name"] for r in captured["rows"]] == [
        "Java开发工程师"
    ]
    assert result["blocked_jds"] == 1
    assert result["blocked_positions"] == 1
    assert result["unvalidated_jds"] == 0
    assert captured["write"] is True


def test_emerging_positions_queried_and_relaxed(monkeypatch):
    """新兴岗位（candidate/emerging）按 0.5 宽松阈值放行。"""
    rows = [_jd_row(0.55, "大模型应用工程师")]
    result, captured = _run(monkeypatch, rows, {"大模型应用工程师"})
    assert len(captured["rows"]) == 1
    assert result["blocked_jds"] == 0


def test_unvalidated_jds_pass_through(monkeypatch):
    """无 cross_validation 的历史 JD 放行（管线已保证验证先行）。"""
    rows = [_jd_row(None, "存量岗位")]
    result, captured = _run(monkeypatch, rows, set())
    assert len(captured["rows"]) == 1
    assert result["unvalidated_jds"] == 1
