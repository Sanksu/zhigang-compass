# -*- coding: utf-8 -*-
"""dict-guard 提案去重/驳回冷却纯函数单测（08-24 重复提案缺口修复）。

实证背景：数据策略/BPEL 08-23 驳回后，08-24 被每日 ETL 重复提议刷池——
原去重只查 pending，不查驳回史。修复后 rejected 进入冷却期不重提。
"""

from datetime import datetime, timedelta, timezone


from app.workers.dict_guard import _reproposal_blocked, _reproposal_skip_reason

_NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


class _Prior:
    def __init__(self, status, reviewed_at=None):
        self.status = status
        self.reviewed_at = reviewed_at


class TestReproposalBlocked:
    def test_none_prior_allows(self):
        assert _reproposal_blocked(None, _NOW) is False

    def test_pending_blocks(self):
        assert _reproposal_blocked(_Prior("pending"), _NOW) is True

    def test_rejected_within_cooldown_blocks(self):
        prior = _Prior("rejected", _NOW - timedelta(days=1))
        assert _reproposal_blocked(prior, _NOW) is True

    def test_rejected_after_cooldown_allows(self):
        prior = _Prior("rejected", _NOW - timedelta(days=8))
        assert _reproposal_blocked(prior, _NOW) is False

    def test_rejected_custom_cooldown(self):
        prior = _Prior("rejected", _NOW - timedelta(days=5))
        assert _reproposal_blocked(prior, _NOW, cooldown_days=3) is False
        assert _reproposal_blocked(prior, _NOW, cooldown_days=10) is True

    def test_approved_allows(self):
        assert _reproposal_blocked(_Prior("approved", _NOW), _NOW) is False

    def test_rejected_missing_reviewed_at_allows(self):
        # 历史数据无审查时间：不构成阻塞，避免永久卡死重提议
        assert _reproposal_blocked(_Prior("rejected", None), _NOW) is False

    def test_rejected_exactly_at_cutoff_blocks(self):
        prior = _Prior("rejected", _NOW - timedelta(days=7))
        assert _reproposal_blocked(prior, _NOW) is True


class TestReproposalSkipReason:
    def test_pending_reason(self):
        assert _reproposal_skip_reason(_Prior("pending")) == "已有待审提案"

    def test_rejected_reason_contains_date(self):
        prior = _Prior("rejected", _NOW - timedelta(days=1))
        reason = _reproposal_skip_reason(prior)
        assert "驳回冷却期" in reason and "2026-08-23" in reason
