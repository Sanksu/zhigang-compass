"""dict-guard 巡检重试（_reconcile_failed_effects）单测。

2026-09-03 非原子性处理：已批准但副作用失败的提案（effects_applied=False）
由每日巡检幂等重试补齐。覆盖：重试成功翻 True、达上限跳过、缺审计记录跳过、
重试再次失败仍置 False 并计数。
"""

import types

import pytest

import app.workers.dict_guard as wg
from app.services import dict_guard_effect as effect


def _proposal(**kw):
    base = dict(
        id="p1", entity_type="skill", term="低代码平台搭建", action="add_stopword",
        status="approved", effects_applied=False, effects_error="neo4j down: timeout",
        effects_retry_count=0, impact_stats={}, reviewed_at=None, reason="r",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _changelog(kind="blocked", term="低代码平台搭建"):
    return types.SimpleNamespace(
        id="c1", kind=kind, term=term, entity_type="skill",
        action="add_stopword", proposal_id="p1",
    )


class _Scalars:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Sess:
    """按行序返回提案 rows；scalar() 依序返回 changelogs（None=缺审计记录）。"""

    def __init__(self, rows, changelogs):
        self.rows = rows
        self._chg = iter(changelogs)
        self._seen = 0
        self.committed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def scalars(self, stmt):
        self._seen += 1
        return _Scalars(self.rows)

    async def scalar(self, stmt):
        return next(self._chg, None)

    async def commit(self):
        self.committed += 1


@pytest.mark.asyncio
async def test_reconcile_retries_failed_and_sets_applied_true(monkeypatch):
    """副作用重试成功 → 提案 effects_applied 翻 True、清空错误、计数+1。"""
    row = _proposal()
    monkeypatch.setattr(effect, "apply_review_effect", lambda **kw: {"removed_nodes": 2})
    sess = _Sess([row], [_changelog()])
    res = await wg._reconcile_failed_effects()
    assert res["reconciled"] == 1
    assert row.effects_applied is True
    assert row.effects_error == ""
    assert row.impact_stats["removed_nodes"] == 2
    assert sess.committed == 1


@pytest.mark.asyncio
async def test_reconcile_skips_when_retry_cap_reached(monkeypatch):
    """重试次数达上限 → 跳过、不改状态（交人工）。"""
    row = _proposal(effects_retry_count=wg._EFFECT_MAX_RETRY)
    res = await wg._reconcile_failed_effects()
    assert res["reconciled"] == 0 and res["skipped"] == 1
    assert row.effects_applied is False


@pytest.mark.asyncio
async def test_reconcile_skips_missing_changelog(monkeypatch):
    """缺关联审计记录 → 无法确定副作用形态，不盲目重放（防误删），交人工。"""
    row = _proposal()
    res = await wg._reconcile_failed_effects()
    assert res["reconciled"] == 0 and res["skipped"] == 1
    assert row.effects_retry_count == wg._EFFECT_MAX_RETRY  # 封顶交人工
    assert "审计" in row.effects_error


@pytest.mark.asyncio
async def test_reconcile_keeps_failed_on_retry_error(monkeypatch):
    """重试再次抛异常 → 仍置 False、计数+1、错误更新（下次再来）。"""
    row = _proposal(effects_retry_count=1)
    monkeypatch.setattr(effect, "apply_review_effect", lambda **kw: (_ for _ in ()).throw(RuntimeError("still down")))
    res = await wg._reconcile_failed_effects()
    assert res["still_failed"] == 1
    assert row.effects_applied is False
    assert row.effects_retry_count == 2
    assert "still down" in row.effects_error