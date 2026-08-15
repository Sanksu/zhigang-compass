"""discovery_auto_transition ARQ 任务端到端测试（设计文档 7.2.1/7.2.4）。

通过 mock 数据库层（PostgreSQL async_session_factory + Neo4j driver），
用 jd_raw 已抽取记录的 post_date 聚合岗位 30 天窗口发布频次，验证
emerging → stable 自动升级链路在真实任务函数内完整生效：

    jd_raw post_date → jd_publish_windows → evaluate_auto_transition
    → PositionStateMachine.persist（Neo4j MERGE）→ 候选池状态落库

信号源说明（2026-08-11）：declining 判定信号从图谱快照边数改为真实 JD
发布数（快照边数随图谱清理/重建波动伪降，发布数语义 = "JD 需求下降"）。
不依赖真实基础设施，全部 DB 交互由 fake 捕获断言。
"""

import pytest
import asyncio
import unittest.mock as mock
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.workers.tasks import discovery_auto_transition

_TZ_CN = timezone(timedelta(hours=8))
# 窗口终点基准（jd_publish_windows 以数据最晚日为 end）
_END = datetime(2026, 8, 11, tzinfo=_TZ_CN)


class _SeqResult:
    """next_id 的 Counter 查询结果桩（single 返回 seq）。"""

    def __init__(self, seq: int):
        self._seq = seq

    def single(self):
        return {"seq": self._seq}


def _jd_row(name: str, post_date: str) -> SimpleNamespace:
    """构造一条已抽取 JDRaw：post_date 用于窗口聚合，extraction 含岗位名。"""
    return SimpleNamespace(
        id=1,
        snapshot={"post_date": post_date, "extraction": {"position_name": name}},
        created_at=_END,
    )


def _jd_rows_by_window(name: str, window_counts: list[int]) -> list:
    """按窗口频次构造 jd_raw 记录。

    window_counts[i] = 窗口 i 的发布数（窗口 0 为最近窗口）。基准日：
    窗口 0→2026-08-10、窗口1→2026-06-20、窗口2→2026-05-20、窗口3→2026-04-20
    （对应 30 天窗口：距 end 0/52/83/113 天 → idx 0/1/2/3）。
    """
    days = {0: "2026-08-10", 1: "2026-06-20", 2: "2026-05-20", 3: "2026-04-20"}
    rows = []
    for i, count in enumerate(window_counts):
        for _ in range(count):
            rows.append(_jd_row(name, days[i]))
    return rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    """AsyncSession fake：先后被两个 async with 复用，scalars 按顺序返回。"""

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
        # next_id 的 Counter 自增查询（08-14：persist 创建时补全 id/freq）
        if "Counter" in query:
            return _SeqResult(1)


class _FakeNeo4jSession:
    def __init__(self, queries, rows_by_query: dict | None = None):
        self._queries = queries
        # skill_novelty 查询返回（08-15）：按 Cypher 关键字匹配——REQUIRES
        # 查询返回岗位技能映射、first_seen 查询返回技能首见时间
        self._rows_by_query = rows_by_query or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_write(self, fn):
        fn(_FakeTx(self._queries))

    def run(self, query, **params):
        self._queries.append((query, params))
        for key, rows in self._rows_by_query.items():
            if key in query:
                return _FakeRows(rows)
        return _FakeRows([])


class _FakeRows:
    """session.run().data() 桩。"""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def data(self):
        return self._rows


class _FakeDriver:
    def __init__(self, rows_by_query: dict | None = None):
        self.queries = []
        self._rows_by_query = rows_by_query

    def session(self):
        return _FakeNeo4jSession(self.queries, self._rows_by_query)


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


def _run_task(sessions, driver) -> dict:
    """在 patch 数据库层后以 asyncio.run 执行任务（项目无 pytest-asyncio auto 模式）。

    任务内两次 `async with async_session_factory()`（先查 jd_raw、再查候选），
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
    def test_promotes_emerging_to_stable_across_four_windows(self):
        """4 个 30 天窗口发布频次平稳 + 高置信 → 任务将 emerging 升级为 stable。

        验证：Neo4j 收到 MERGE 且 state=stable；候选池落库为 stable；
        返回 transitions=1 与明细。
        """
        name = "RAG"
        jd_session = _FakeSession(_jd_rows_by_window(name, [11, 10, 11, 10]))
        row = _candidate_row(name)
        cand_session = _FakeSession([row])
        driver = _FakeDriver()

        result = _run_task([jd_session, cand_session], driver)

        assert result["transitions"] == 1
        assert result["detail"] == [{
            "position_name": name,
            "from_state": "emerging",
            "to_state": "stable",
        }]
        # 候选池状态落库 + Neo4j 幂等 MERGE（08-14：Counter 自增 + MERGE 共 2 条）
        assert row.state == "stable"
        assert cand_session.committed is True
        assert len(driver.queries) == 3
        query, params = driver.queries[2]
        assert "MERGE (p:Position {name: $name})" in query
        assert "SET p.status = $state" in query
        assert params["name"] == name
        assert params["state"] == "stable"

    def test_cold_start_skips_without_jd_records(self):
        """jd_raw 无已抽取记录（冷启动）→ 直接返回，不查询候选池、不产生副作用。"""
        jd_session = _FakeSession([])
        # 候选查询不应被触发：第二次 factory 调用若发生则 pop 空列表出错
        cand_session = _FakeSession([])
        driver = _FakeDriver()

        result = _run_task([jd_session, cand_session], driver)

        assert result["transitions"] == 0
        assert "冷启动" in result["detail"]
        assert driver.queries == []
        assert cand_session.committed is False

    def test_volatile_windows_not_promoted(self):
        """3 窗口发布频次波动大（> 25%）→ 判定不升级，transitions=0。"""
        name = "RAG"
        jd_session = _FakeSession(_jd_rows_by_window(name, [10, 6, 10]))
        row = _candidate_row(name)
        cand_session = _FakeSession([row])
        driver = _FakeDriver()

        result = _run_task([jd_session, cand_session], driver)

        assert result["transitions"] == 0
        assert result["detail"] == []
        assert row.state == "emerging"
        # 08-15：novelty 批量查询执行（REQUIRES），但不迁移则无 persist 写入
        assert all("MERGE" not in q for q, _ in driver.queries)

    def test_recovery_from_declining_to_stable(self):
        """发布频次先降后升（最近 2 窗口 z > 0）→ declining 自动回迁 stable。"""
        name = "RAG"
        jd_session = _FakeSession(_jd_rows_by_window(name, [8, 8, 4, 10]))
        row = _candidate_row(name, state="declining")
        cand_session = _FakeSession([row])
        driver = _FakeDriver()

        result = _run_task([jd_session, cand_session], driver)

        assert result["transitions"] == 1
        assert result["detail"] == [{
            "position_name": name,
            "from_state": "declining",
            "to_state": "stable",
        }]
        assert row.state == "stable"
        assert cand_session.committed is True
        assert len(driver.queries) == 3  # 08-15：novelty REQUIRES 查询 + Counter 自增 + MERGE
        query, params = driver.queries[2]
        assert "SET p.status = $state" in query
        assert params["state"] == "stable"

    def test_non_migratable_state_ignored(self):
        """候选池仅含 candidate（非自动可迁移状态）→ 不处理。"""
        name = "RAG"
        jd_session = _FakeSession(_jd_rows_by_window(name, [10, 10, 10]))
        # 任务只查询 emerging/stable/declining，candidate 不会被选中
        cand_session = _FakeSession([])
        driver = _FakeDriver()

        result = _run_task([jd_session, cand_session], driver)

        assert result["transitions"] == 0
        # 08-15：novelty 批量查询在循环前无条件执行（空岗位集发 1 条
        # REQUIRES 查询），但不得有任何 persist 写入（MERGE）
        assert all("MERGE" not in q for q, _ in driver.queries)

class TestPositionSkillNovelty:
    """_position_skill_novelty 计算（§7.2.1：Skill.first_seen 平均图谱年龄归一化）。"""

    def _run(self, position_rows, first_seen_rows, names=None):
        from app.workers.tasks import _position_skill_novelty

        class _S:
            def __init__(self, rows_by_query):
                self._map = rows_by_query

            def run(self, query, **params):
                for key, rows in self._map.items():
                    if key in query:
                        return _FakeRows(rows)
                return _FakeRows([])

        rows_by_query = {"REQUIRES": position_rows, "first_seen": first_seen_rows}
        return _position_skill_novelty(_S(rows_by_query), names or ["岗位A"])

    def test_mature_skills_low_novelty(self):
        """技能平均年龄 ≥ 255 天（novelty < 0.3）→ 可 stable。"""
        from datetime import date, timedelta
        old = date.today() - timedelta(days=400)
        out = self._run(
            [{"pname": "岗位A", "skills": ["Python", "SQL"]}],
            [{"name": "Python", "first_seen": old.isoformat()},
             {"name": "SQL", "first_seen": old.isoformat()}],
        )
        assert out["岗位A"] == 0.0  # 1 - min(400/365, 1) = 0

    def test_new_skills_high_novelty(self):
        """技能平均年龄小（novelty ≥ 0.3）→ 拦截 stable。"""
        from datetime import date, timedelta
        recent = date.today() - timedelta(days=30)
        out = self._run(
            [{"pname": "岗位A", "skills": ["AI 原生", "多模态"]}],
            [{"name": "AI 原生", "first_seen": recent.isoformat()},
             {"name": "多模态", "first_seen": recent.isoformat()}],
        )
        assert out["岗位A"] > 0.3

    def test_mixed_skills_average(self):
        """新老技能混合取平均。"""
        from datetime import date, timedelta
        old = date.today() - timedelta(days=400)
        recent = date.today() - timedelta(days=40)
        out = self._run(
            [{"pname": "岗位A", "skills": ["Python", "AI 原生"]}],
            [{"name": "Python", "first_seen": old.isoformat()},
             {"name": "AI 原生", "first_seen": recent.isoformat()}],
        )
        avg = (400 + 40) / 2
        assert out["岗位A"] == pytest.approx(1 - min(avg / 365, 1.0), abs=1e-3)

    def test_no_skills_returns_none(self):
        """岗位无 REQUIRES 技能 → None（判定层不拦截）。"""
        out = self._run([{"pname": "岗位A", "skills": []}], [])
        assert out["岗位A"] is None

    def test_missing_first_seen_skipped(self):
        """first_seen 缺失的技能不参与平均；全缺失 → None。"""
        out = self._run(
            [{"pname": "岗位A", "skills": ["Python", "SQL"]}],
            [{"name": "Python", "first_seen": None}],
        )
        assert out["岗位A"] is None


class TestAutoTransitionTaskNoveltyGate:
    """任务级：skill_novelty ≥ 0.3 的岗位即使其他条件达标也不升级 stable。"""

    def test_emerging_not_promoted_when_novelty_high(self):
        name = "RAG"
        jd_session = _FakeSession(_jd_rows_by_window(name, [11, 10, 11, 10]))
        row = _candidate_row(name)
        cand_session = _FakeSession([row])
        from datetime import date, timedelta
        recent = date.today() - timedelta(days=20)
        driver = _FakeDriver(rows_by_query={
            "REQUIRES": [{"pname": name, "skills": ["AI 原生"]}],
            "first_seen": [{"name": "AI 原生", "first_seen": recent.isoformat()}],
        })

        result = _run_task([jd_session, cand_session], driver)

        assert result["transitions"] == 0
        assert row.state == "emerging"  # 未升级
