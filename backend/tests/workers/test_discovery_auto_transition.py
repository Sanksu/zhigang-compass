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
from tests.helpers import SeqResult
import asyncio
import unittest.mock as mock
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.workers.discovery import (
    _rename_candidates_for_current_normalization,
    discovery_auto_transition,
)

_TZ_CN = timezone(timedelta(hours=8))
# 窗口终点基准（jd_publish_windows 以数据最晚日为 end）
_END = datetime(2026, 8, 11, tzinfo=_TZ_CN)




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
            return SeqResult(1)


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
        assert len(driver.queries) == 4  # 方案 A（09-02）：多 1 条图谱岗位名查询排最后
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
        """3 窗口末窗显著萎缩（> 25%）→ 判定不升级，transitions=0。

        08-19 口径修正：波动只惩罚萎缩（增长/首采接入不算不稳定），
        故用萎缩序列 [10,9,6]（末窗 9→6 萎缩 33% > 25%，decline 40% 未
        超 declining 门槛，留在 emerging）验证"不稳定不升级"。
        """
        name = "RAG"
        jd_session = _FakeSession(_jd_rows_by_window(name, [6, 10, 9]))  # [9,10,6]: 最近窗口 10→6 萎缩 40%>25%
        row = _candidate_row(name)
        cand_session = _FakeSession([row])
        driver = _FakeDriver()

        result = _run_task([jd_session, cand_session], driver)

        assert result["transitions"] == 0
        assert result["detail"] == []
        assert row.state == "emerging"
        # 08-15：novelty 批量查询执行（REQUIRES），但不迁移则无 persist 写入
        assert all("MERGE" not in q for q, _ in driver.queries)

    def test_jd_count_below_threshold_not_promoted(self):
        """JD 总数 < 5（§7.2.1 jd_count 门槛）即使波动/源多样性全达标也不升级。

        任务级对照：单测覆盖 =5 边界迁移，此处验证 <5 不迁移——3 窗口各 1 条
        （jd_count=3，波动 0、decline 0），仅小基数门槛拦截。
        """
        name = "RAG"
        jd_session = _FakeSession(_jd_rows_by_window(name, [1, 1, 1]))
        row = _candidate_row(name)
        cand_session = _FakeSession([row])
        driver = _FakeDriver()

        result = _run_task([jd_session, cand_session], driver)

        assert result["transitions"] == 0
        assert result["detail"] == []
        assert row.state == "emerging"  # 未升级（jd_count=3 < 5）
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
        assert len(driver.queries) == 4  # 方案 A（09-02）：多 1 条图谱岗位名查询排最后
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

    def _run(self, position_rows, first_seen_rows, names=None, reference_days=None):
        from app.workers.discovery import _position_skill_novelty

        class _S:
            def __init__(self, rows_by_query):
                self._map = rows_by_query

            def run(self, query, **params):
                for key, rows in self._map.items():
                    if key in query:
                        return _FakeRows(rows)
                return _FakeRows([])

        rows_by_query = {"REQUIRES": position_rows, "first_seen": first_seen_rows}
        return _position_skill_novelty(_S(rows_by_query), names or ["岗位A"], reference_days=reference_days)

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
        """技能平均年龄小（novelty ≥ 0.3）→ 拦截 stable（固定 365 参考周期）。"""
        from datetime import date, timedelta
        recent = date.today() - timedelta(days=30)
        out = self._run(
            [{"pname": "岗位A", "skills": ["AI 原生", "多模态"]}],
            [{"name": "AI 原生", "first_seen": recent.isoformat()},
             {"name": "多模态", "first_seen": recent.isoformat()}],
            reference_days=365,
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
            reference_days=365,
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
    def test_adaptive_reference_cold_start(self):
        """自适应参考周期（冷启动）：图谱早期存量技能 novelty=0（可 stable）。

        图谱仅运行 33 天时固定 365 天参考周期会让全部技能 novelty≈0.99
        （实测），任何岗位无法 stable——自适应后首日技能 novelty=0。
        """
        from datetime import date, timedelta
        start = date.today() - timedelta(days=33)
        recent = date.today() - timedelta(days=2)
        out = self._run(
            [{"pname": "岗位A", "skills": ["Vue", "AI 原生"]}],
            [{"name": "Vue", "first_seen": start.isoformat()},
             {"name": "AI 原生", "first_seen": recent.isoformat()}],
        )
        # 参考周期 = 33 天（图谱生命周期）；Vue 首日出现 → avg_age 17.5 → novelty ≈ 0.47
        assert out["岗位A"] is not None
        assert 0.3 < out["岗位A"] < 0.6

    def test_adaptive_reference_earliest_skill_mature(self):
        """图谱首日即有的存量技能 → novelty=0（成熟可 stable）。"""
        from datetime import date, timedelta
        start = date.today() - timedelta(days=33)
        out = self._run(
            [{"pname": "岗位A", "skills": ["Vue"]}],
            [{"name": "Vue", "first_seen": start.isoformat()}],
        )
        assert out["岗位A"] == 0.0

class TestAutoTransitionTaskNoveltyGate:
    """任务级：skill_novelty ≥ 0.3 的岗位即使其他条件达标也不升级 stable。"""

    def test_emerging_not_promoted_when_novelty_high(self):
        name = "RAG"
        jd_session = _FakeSession(_jd_rows_by_window(name, [11, 10, 11, 10]))
        row = _candidate_row(name)
        cand_session = _FakeSession([row])
        from datetime import date, timedelta
        start = date.today() - timedelta(days=30)
        recent = date.today() - timedelta(days=2)
        driver = _FakeDriver(rows_by_query={
            "REQUIRES": [{"pname": name, "skills": ["Vue", "AI 原生"]}],
            "first_seen": [{"name": "Vue", "first_seen": start.isoformat()},
                           {"name": "AI 原生", "first_seen": recent.isoformat()}],
        })

        result = _run_task([jd_session, cand_session], driver)

        assert result["transitions"] == 0
        assert row.state == "emerging"  # 未升级


class TestCandidateNormalizationMigration:
    """候选池键随岗位归一化升级的受控迁移。"""

    @staticmethod
    def _snapshot(old_name: str, raw_name: str) -> dict:
        return {
            "normalized_position": old_name,
            "normalized_position_meta": {"version": "2026-08-22.1"},
            "extraction": {"position_name": raw_name, "skills": []},
        }

    def test_renames_candidate_for_unambiguous_old_to_new_mapping(self):
        row = _candidate_row("前端工程师")
        snapshot = self._snapshot("前端工程师", "React 前端工程师")

        with mock.patch(
            "app.services.extraction.position_normalization.normalize_position_name",
            return_value="React 前端工程师",
        ):
            renames, ambiguous = _rename_candidates_for_current_normalization([row], [snapshot])

        assert renames == {"前端工程师": "React 前端工程师"}
        assert ambiguous == {}
        assert row.position_name == "React 前端工程师"

    def test_preserves_candidate_when_old_name_splits_to_multiple_current_names(self):
        row = _candidate_row("前端工程师")
        snapshots = [
            self._snapshot("前端工程师", "React 前端工程师"),
            self._snapshot("前端工程师", "Vue 前端工程师"),
        ]
        with mock.patch(
            "app.services.extraction.position_normalization.normalize_position_name",
            side_effect=["React 前端工程师", "Vue 前端工程师"],
        ):
            renames, ambiguous = _rename_candidates_for_current_normalization([row], snapshots)

        assert renames == {}
        assert ambiguous == {"前端工程师": {"React 前端工程师", "Vue 前端工程师"}}
        assert row.position_name == "前端工程师"

    def test_preserves_candidate_when_new_key_already_exists(self):
        legacy = _candidate_row("前端工程师")
        current = _candidate_row("React 前端工程师")
        snapshot = self._snapshot("前端工程师", "React 前端工程师")
        with mock.patch(
            "app.services.extraction.position_normalization.normalize_position_name",
            return_value="React 前端工程师",
        ):
            renames, ambiguous = _rename_candidates_for_current_normalization(
                [legacy, current], [snapshot],
            )

        assert renames == {}
        assert ambiguous == {"前端工程师": {"React 前端工程师"}}
        assert legacy.position_name == "前端工程师"
        assert current.position_name == "React 前端工程师"

    def test_preserves_all_candidates_when_multiple_old_names_converge_on_new_key(self):
        """A→C、B→C 且 C 初始不存在时不触发唯一键冲突。"""
        first = _candidate_row("旧岗位 A")
        second = _candidate_row("旧岗位 B")
        snapshots = [
            self._snapshot("旧岗位 A", "新岗位 C"),
            self._snapshot("旧岗位 B", "新岗位 C"),
        ]
        with mock.patch(
            "app.services.extraction.position_normalization.normalize_position_name",
            return_value="新岗位 C",
        ):
            renames, ambiguous = _rename_candidates_for_current_normalization(
                [first, second], snapshots,
            )

        assert renames == {}
        assert ambiguous == {"旧岗位 A": {"新岗位 C"}, "旧岗位 B": {"新岗位 C"}}
        assert first.position_name == "旧岗位 A"
        assert second.position_name == "旧岗位 B"

    def test_transition_uses_current_frequency_window_after_rename(self):
        """一对一迁移后，旧候选使用新版岗位名的 JD 窗口完成状态流转。"""
        old_name = "前端工程师"
        current_name = "React 前端工程师"
        jd_rows = _jd_rows_by_window(current_name, [11, 10, 11, 10])
        for row in jd_rows:
            row.snapshot["normalized_position"] = old_name
            row.snapshot["normalized_position_meta"] = {"version": "2026-08-22.1"}
        candidate = _candidate_row(old_name)
        jd_session = _FakeSession(jd_rows)
        candidate_session = _FakeSession([candidate])
        driver = _FakeDriver()

        with mock.patch(
            "app.services.extraction.position_normalization.normalize_position_name",
            return_value=current_name,
        ):
            result = _run_task([jd_session, candidate_session], driver)

        assert result["transitions"] == 1
        assert candidate.position_name == current_name
        assert candidate.state == "stable"
        # 方案 A（09-02）：最后一条是图谱岗位名查询，MERGE 在倒数第二条
        assert driver.queries[-2][1]["name"] == current_name
