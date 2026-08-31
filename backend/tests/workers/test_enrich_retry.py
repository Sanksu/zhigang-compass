"""enrich_course_skills 失败延迟重试测试（08-16 用户要求）。

覆盖：LLM 抽取失败 → 写失败计数 + 延迟重试时间戳（不标记完成）；
累计失败达上限 → 放弃（标记 skills_enriched 防无限重试）；
LLM 不可用 → 不标记（配置恢复后重试，既有语义保持）。
"""

import asyncio
import unittest.mock as mock
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.workers.tasks import (
    _EMPTY_MAX_RETRIES,
    _EMPTY_RETRY_DELAY_SECONDS,
    _ENRICH_MAX_FAILS,
    _ENRICH_RETRY_DELAY_SECONDS,
    enrich_course_skills,
)

_TZ_CN = timezone(timedelta(hours=8))


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _FakeSession:
    """AsyncSession 桩：两次 async with（查询 + 写回），捕获写回对象。"""

    def __init__(self, rows):
        self._rows = rows
        self.written: list[SimpleNamespace] = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalars(self, stmt):
        return _FakeResult(self._rows)

    async def commit(self):
        self.committed = True


def _course(sid: str, title: str, snap: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=sid, snapshot=snap or {"title": title})


def _run(rows, llm_ok: bool = True, fail: bool = False, empty: bool = False) -> tuple[dict, _FakeSession]:
    """执行任务：llm_ok=False 模拟 LLM 配置缺失；fail=True 模拟抽取抛异常；
    empty=True 模拟 LLM 正常判定无技能（宁少勿滥空数组）。"""

    def _factory():
        s = sessions.pop(0) if sessions else _FakeSession([])
        calls.append(s)
        return s

    jd_factory = mock.Mock()
    if llm_ok:
        jd_factory.return_value.llm = mock.Mock()
    else:
        jd_factory.return_value.llm = None

    extract = mock.Mock()
    if fail:
        extract.side_effect = RuntimeError("LLM provider 超时")
    elif empty:
        extract.return_value = []
    else:
        extract.return_value = ["Python"]

    sessions = [_FakeSession(rows), _FakeSession(rows)]
    calls: list[_FakeSession] = []
    with (
        mock.patch("app.core.database.async_session_factory", side_effect=_factory),
        # 函数内局部导入：patch 模块属性（调用时解析）
        mock.patch("app.services.extraction.jd_extractor.JDExtractor", jd_factory),
        mock.patch("app.services.extraction.course_skills.extract_course_skills", extract),
    ):
        result = asyncio.run(enrich_course_skills({}))
    # 写回修改的是同一批课程对象（两个 session 共享 rows 引用）
    return result, rows


class TestEnrichRetry:
    def test_failure_writes_fail_count_and_retry_at(self):
        """LLM 失败：写 skills_enrich_fails=1 + skills_retry_at（未来），不标记完成。"""
        result, sess = _run([_course("c1", "课程A")], fail=True)
        assert result["failed"] == 1
        snap = sess[0].snapshot
        assert snap["skills_enrich_fails"] == 1
        assert "skills_enriched" not in snap  # 失败不标记（可重试）
        retry_at = datetime.fromisoformat(snap["skills_retry_at"])
        # 延迟时间 = 配置值（未来）
        assert retry_at > datetime.now(_TZ_CN)
        assert (retry_at - datetime.now(_TZ_CN)).total_seconds() > _ENRICH_RETRY_DELAY_SECONDS - 60

    def test_failure_at_max_gives_up(self):
        """累计失败达上限：标记 skills_enriched（放弃，防无限重试）。"""
        snap = {"title": "课程A", "skills_enrich_fails": _ENRICH_MAX_FAILS - 1}
        result, sess = _run([_course("c1", "课程A", snap)], fail=True)
        assert result["failed"] == 1
        assert sess[0].snapshot["skills_enriched"] is True
        assert sess[0].snapshot["skills_enrich_fails"] == _ENRICH_MAX_FAILS

    def test_success_clears_retry_state(self):
        """成功：写 skills + 标记完成（无 retry_at 残留）。"""
        result, sess = _run([_course("c1", "课程A")])
        assert result["enriched"] == 1
        snap = sess[0].snapshot
        assert snap["skills"] == ["Python"]
        assert snap["skills_enriched"] is True
        assert "skills_retry_at" not in snap

    def test_llm_unavailable_not_marked(self):
        """LLM 配置缺失：不写标记（配置恢复后自动重试，既有语义）。"""
        result, sess = _run([_course("c1", "课程A")], llm_ok=False)
        assert result["skipped_no_llm"] == 1
        assert sess[0].snapshot == {"title": "课程A"}  # 未写回任何状态

    def test_retry_delay_configured(self):
        """配置常量存在且为正（调度可读）。"""
        assert _ENRICH_RETRY_DELAY_SECONDS > 0
        assert _ENRICH_MAX_FAILS >= 1

    def test_empty_judged_writes_lag_retry_not_done(self):
        """判定为空：写计数 + 24h 冷却时间戳，不永久标记（可滞后重抽）。"""
        result, sess = _run([_course("c1", "课程A")], empty=True)
        assert result["failed"] == 0
        snap = sess[0].snapshot
        assert snap["skills_enrich_empty_fails"] == 1
        assert "skills_enriched" not in snap  # 未永久标记（重试点保留资格）
        retry_at = datetime.fromisoformat(snap["skills_retry_at"])
        # 滞后冷却 = 24h（未来）
        assert retry_at > datetime.now(_TZ_CN)
        assert (retry_at - datetime.now(_TZ_CN)).total_seconds() > _EMPTY_RETRY_DELAY_SECONDS - 60

    def test_empty_judged_at_max_gives_up(self):
        """判定为空累计达上限：永久标记（放弃，防无限重抽）。"""
        snap = {"title": "课程A", "skills_enrich_empty_fails": _EMPTY_MAX_RETRIES - 1}
        result, sess = _run([_course("c1", "课程A", snap)], empty=True)
        assert sess[0].snapshot["skills_enriched"] is True
        assert sess[0].snapshot["skills_enrich_empty_fails"] == _EMPTY_MAX_RETRIES

    def test_error_and_empty_counts_are_independent(self):
        """P1：既有 error 计数不抬高空路径放弃——首个空不永久标记、error 计数不受影响。"""
        snap = {"title": "课程A", "skills_enrich_fails": 1}  # 历史 1 次 LLM 异常失败
        result, sess = _run([_course("c1", "课程A", snap)], empty=True)
        assert result["failed"] == 0
        now_snap = sess[0].snapshot
        assert now_snap["skills_enrich_fails"] == 1          # error 计数独立保留
        assert now_snap["skills_enrich_empty_fails"] == 1     # 空计数从 0 起算
        assert "skills_enriched" not in now_snap              # 未过早永久放弃
        assert "skills_retry_at" in now_snap                  # 进入 24h 滞后重抽

    def test_empty_judged_then_success_clears_retry(self):
        """滞后冷却重抽后成功：写 skills + 标记完成，清除 retry 状态。"""
        snap = {"title": "课程A", "skills_enrich_empty_fails": 1}
        # 重抽时 LLM 命中技能 → 成功
        result, sess = _run([_course("c1", "课程A", snap)])
        assert result["enriched"] == 1
        now_snap = sess[0].snapshot
        assert now_snap["skills"] == ["Python"]
        assert now_snap["skills_enriched"] is True
        assert "skills_retry_at" not in now_snap

    def test_empty_lag_constants_configured(self):
        """滞后重抽常量存在且为正。"""
        assert _EMPTY_MAX_RETRIES >= 1
        assert _EMPTY_RETRY_DELAY_SECONDS > 0
