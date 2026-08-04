"""discovery_auto_transition ARQ 任务端到端测试（设计文档 7.2.1/7.2.4）。

通过 mock 数据库层（PostgreSQL async_session_factory + Neo4j driver），
用 3 期以上 graph_versions 快照重建岗位频次窗口，验证 emerging → stable
自动升级链路在真实任务函数内完整生效：

    graph_versions 快照序列 → position_freq_windows → evaluate_auto_transition
    → PositionStateMachine.persist（Neo4j MERGE）→ 候选池状态落库

不依赖真实基础设施，全部 DB 交互由 fake 捕获断言。
"""

import asyncio
import unittest.mock as mock
from types import SimpleNamespace

from app.workers.tasks import discovery_auto_transition


def _snapshot_json(name: str, edge_count: int) -> dict:
    """构造一期 graph_versions.snapshot_json（岗位节点 + 技能边）。"""
    pos_id = "pos_rag"
    return {
        "nodes": [{"id": pos_id, "name": name, "type": "position"}],
        "edges": [{"source": pos_id, "target": f"sk_{i}"} for i in range(edge_count)],
    }


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    """AsyncSession fake：可先后被两个 async with 复用，scalars 按顺序返回。"""

    def __init__(self, *results):
        self._results = list(results)
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalars(self, stmt):
        return _FakeResult(self._results.pop(0))

    async def commit(self):
        self.committed = True


class _FakeTx:
    def __init__(self, queries):
        self._queries = queries

    def run(self, query, **params):
        self._queries.append((query, params))


class _FakeNeo4jSession:
    def __init__(self, queries):
        self._queries = queries

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_write(self, fn):
        fn(_FakeTx(self._queries))


class _FakeDriver:
    def __init__(self):
        self.queries = []

    def session(self):
        return _FakeNeo4jSession(self.queries)


def _candidate_row(name: str = "RAG", state: str = "emerging", confidence: float = 0.9):
    return SimpleNamespace(
        id="cand-rag",
        position_name=name,
        state=state,
        features={"jd_freq_ma3": 12.0, "z_score": 2.5, "source_diversity": 3},
        confidence={"final_confidence": confidence},
        detected_at="2026-07-01T00:00:00+08:00",
        evidence_refs=[],
        seed_matched=True,
        rag_matched=True,
        definition_draft="RAG 工程师负责检索增强生成系统的构建与优化。",
    )


def _snapshot_row(snapshot: dict, created_at: str):
    return SimpleNamespace(snapshot_json=snapshot, created_at=created_at)


def _run_task(sessions, driver) -> dict:
    """在 patch 数据库层后以 asyncio.run 执行任务（项目无 pytest-asyncio auto 模式）。

    任务内两次 `async with async_session_factory()`（先查快照、再查候选），
    故 sessions 需按调用顺序提供两个 fake session。
    """

    def _factory():
        return sessions.pop(0)

    with (
        mock.patch("app.core.database.async_session_factory", side_effect=_factory),
        mock.patch("app.core.database.neo4j_driver", driver),
    ):
        return asyncio.run(discovery_auto_transition({}))


class TestAutoTransitionTask:
    def test_promotes_emerging_to_stable_across_four_snapshots(self):
        """4 期快照频次平稳 + 高置信 → 任务将 emerging 升级为 stable。

        验证：Neo4j 收到 MERGE 且 state=stable；候选池落库为 stable；
        返回 transitions=1 与明细。
        """
        name = "RAG"
        snaps = [
            _snapshot_row(_snapshot_json(name, 10), "2026-07-01T00:00:00+08:00"),
            _snapshot_row(_snapshot_json(name, 11), "2026-07-11T00:00:00+08:00"),
            _snapshot_row(_snapshot_json(name, 10), "2026-07-21T00:00:00+08:00"),
            _snapshot_row(_snapshot_json(name, 11), "2026-08-01T00:00:00+08:00"),
        ]
        row = _candidate_row(name)
        snap_session = _FakeSession(snaps)
        cand_session = _FakeSession([row])
        driver = _FakeDriver()

        result = _run_task([snap_session, cand_session], driver)

        assert result["transitions"] == 1
        assert result["detail"] == [{
            "position_name": name,
            "from_state": "emerging",
            "to_state": "stable",
        }]
        # 候选池状态落库 + Neo4j 幂等 MERGE
        assert row.state == "stable"
        assert cand_session.committed is True
        assert len(driver.queries) == 1
        query, params = driver.queries[0]
        assert "MERGE (p:Position {name: $name})" in query
        assert "SET p.status = $state" in query
        assert params["name"] == name
        assert params["state"] == "stable"

    def test_cold_start_skips_when_fewer_than_two_snapshots(self):
        """快照 < 2 期（冷启动）→ 直接返回，不查询候选池、不产生副作用。"""
        name = "RAG"
        snaps = [_snapshot_row(_snapshot_json(name, 10), "2026-07-01T00:00:00+08:00")]
        # 候选查询不应被触发：第二次 factory 调用若发生则 pop 空列表出错
        snap_session = _FakeSession(snaps)
        cand_session = _FakeSession([])
        driver = _FakeDriver()

        result = _run_task([snap_session, cand_session], driver)

        assert result["transitions"] == 0
        assert "冷启动" in result["detail"]
        assert driver.queries == []
        assert cand_session.committed is False

    def test_volatile_windows_not_promoted(self):
        """3 期快照波动大（> 25%）→ 判定不升级，transitions=0。"""
        name = "RAG"
        snaps = [
            _snapshot_row(_snapshot_json(name, 10), "2026-07-01T00:00:00+08:00"),
            _snapshot_row(_snapshot_json(name, 6), "2026-07-11T00:00:00+08:00"),
            _snapshot_row(_snapshot_json(name, 10), "2026-07-21T00:00:00+08:00"),
        ]
        row = _candidate_row(name)
        snap_session = _FakeSession(snaps)
        cand_session = _FakeSession([row])
        driver = _FakeDriver()

        result = _run_task([snap_session, cand_session], driver)

        assert result["transitions"] == 0
        assert result["detail"] == []
        assert row.state == "emerging"
        assert driver.queries == []

    def test_non_migratable_state_ignored(self):
        """候选池仅含 candidate（非自动可迁移状态）→ 不处理。"""
        name = "RAG"
        snaps = [
            _snapshot_row(_snapshot_json(name, 10), "2026-07-01T00:00:00+08:00"),
            _snapshot_row(_snapshot_json(name, 11), "2026-07-11T00:00:00+08:00"),
            _snapshot_row(_snapshot_json(name, 10), "2026-07-21T00:00:00+08:00"),
        ]
        # 任务只查询 emerging/stable/declining，candidate 不会被选中
        snap_session = _FakeSession(snaps)
        cand_session = _FakeSession([])
        driver = _FakeDriver()

        result = _run_task([snap_session, cand_session], driver)

        assert result["transitions"] == 0
        assert driver.queries == []
