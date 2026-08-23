"""watch_signal_daily 观察池提升链路回归测试（设计文档 7.2.5 / P1 修复）。

通过 mock 数据库层（PostgreSQL async_session_factory），验证 watch_signal_daily
提升 candidate 时写入**真实特征与置信度**（而非早期硬编码的
source_diversity=1 / final_confidence=0.0），使提升候选能够通过
can_promote_to_emerging（置信度 ≥ 0.6 AND 源 ≥ 2）：

    raw 4 源周频次 → build_signals → promotable_skills
    → promotion_features（真实 source_diversity / jd_freq_ma3 / growth / z）
    → compute_confidence → DiscoveryCandidate 落库

不依赖真实基础设施，全部 DB 交互由 fake 捕获断言。
"""

import asyncio
import unittest.mock as mock

from app.workers.discovery import watch_signal_daily


class _FakeScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    """AsyncSession fake：scalar/scalars 按调用顺序返回预置结果。"""

    def __init__(self, scalar_results, scalars_results):
        self._scalar_results = list(scalar_results)
        self._scalars_results = list(scalars_results)
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalar(self, stmt):
        return self._scalar_results.pop(0)

    async def scalars(self, stmt):
        return _FakeScalarsResult(self._scalars_results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


class _Row:
    """raw 行 fake（含 _RawMixin 字段）。"""

    def __init__(self, source, crawled_at, skills):
        self.source = source
        self.crawled_at = crawled_at
        self.snapshot = {"extraction": {"skills": [{"name": s} for s in skills]}}


# 观察窗口：13 周（JD 3 月移动平均需 12 周窗口 + 1 个移动平均点）
_DATES = [
    "2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26",
    "2026-02-02", "2026-02-09", "2026-02-16", "2026-02-23",
    "2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23",
    "2026-03-30",
]


def _jd_rows(source: str, flat: bool = False) -> list:
    """构造某 JD 平台的周频次序列。

    flat=True：每期 2 条（平稳，不命中）；flat=False：末 2 周暴增
    （50→100，3 月移动平均环比 > 50%，命中 JD 信号）。
    """
    rows = []
    for i, date in enumerate(_DATES):
        n = 2 if flat or i < len(_DATES) - 2 else (50 if i == len(_DATES) - 2 else 100)
        for k in range(n):
            rows.append(_Row(source, date, ["WebGPU"]))
    return rows


def _run_task(sessions) -> dict:
    """在 patch 数据库层后以 asyncio.run 执行任务。

    watch_signal_daily 内两次 `async with async_session_factory()`：
    第 1 次读 4 源 raw 行（4 次 scalars），第 2 次 upsert + 提升
    （scalar 若干 + scalars 2 次），sessions 按调用顺序提供。
    """

    def _factory():
        return sessions.pop(0)

    with mock.patch("app.core.database.async_session_factory", side_effect=_factory):
        return asyncio.run(watch_signal_daily({}))


class TestWatchSignalPromotion:
    def test_promoted_candidate_uses_real_features_and_confidence(self):
        """提升候选写入真实特征/置信度，能通过 emerging 门槛（死路修复）。"""
        # boss 命中 JD 信号（末 2 周暴增）；zhilian 平稳不命中但计入源多样性。
        # 两源周序列合并：jd_freq_ma3 高 + growth 高 + source_diversity=2
        jd_rows = _jd_rows("boss") + _jd_rows("zhilian", flat=True)

        # session A：4 次 scalars 返回 4 源 raw 行
        session_a = _FakeSession(
            scalar_results=[],
            scalars_results=[
                jd_rows,       # JDRaw
                [],            # CourseRaw
                [],            # PaperRaw
                [],            # CommunityRaw
            ],
        )
        # session B：upsert 检查 scalar=None（新增）→ 提升检查 scalar=None；
        # scalars 返回此前观察历史（WebGPU 已在池）与本期 watch 行
        session_b = _FakeSession(
            scalar_results=[None, None],
            scalars_results=[["WebGPU"], []],
        )

        result = _run_task([session_a, session_b])

        assert result["promoted"] == 1
        assert result["detail"] == ["WebGPU"]
        assert session_b.committed is True

        cand = session_b.added[-1]  # 最后 add 的是 DiscoveryCandidate
        assert cand.position_name == "WebGPU"
        # 真实特征（键与 DiscoveryFeatures schema 一致，非硬编码）
        assert cand.features["source_diversity"] == 2
        assert cand.features["jd_freq_ma3"] > 0
        assert cand.features["z_score"] is not None
        assert "jd_mom_growth" not in cand.features
        # 真实置信度（compute_confidence 计算，非 0.0 硬编码）
        assert cand.confidence["final_confidence"] > 0

        # 端到端：真实特征 + 置信度能通过 candidate→emerging 门槛
        from app.services.discovery.schemas import (
            CandidatePosition,
            DiscoveryFeatures,
            PositionState,
        )
        from app.services.discovery.state_machine import can_promote_to_emerging

        candidate = CandidatePosition(
            candidate_id=cand.id,
            position_name="WebGPU",
            state=PositionState.CANDIDATE,
            features=DiscoveryFeatures(**cand.features),
            detected_at=cand.detected_at,
        )
        assert can_promote_to_emerging(
            candidate, confidence=float(cand.confidence["final_confidence"])
        )

    def test_promoted_candidate_marks_watch_row_status(self):
        """提升后本期 watch 行 status 置 candidate_promoted。"""
        jd_rows = _jd_rows("boss")

        session_a = _FakeSession(
            scalar_results=[],
            scalars_results=[jd_rows, [], [], []],
        )
        watch_row = mock.Mock()
        session_b = _FakeSession(
            scalar_results=[None, None],
            scalars_results=[["WebGPU"], [watch_row]],
        )

        result = _run_task([session_a, session_b])

        assert result["promoted"] == 1
        assert watch_row.status == "candidate_promoted"
