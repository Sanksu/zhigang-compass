"""岗位状态机单元测试（设计文档 7.2.1 节）。

覆盖：转换合法性校验、自动转换判定（stable/declining/recovery）、
candidate→emerging 条件、持久化幂等与审计日志。
"""

import pytest

from app.services.discovery.schemas import CandidatePosition, DiscoveryFeatures, PositionState
from app.services.discovery.state_machine import (
    WindowFreq,
    can_promote_to_emerging,
    decline_rate,
    evaluate_auto_transition,
    has_recovery,
    window_volatility,
    PositionStateMachine,
)


def _candidate(state: PositionState, source_diversity: int = 2, confidence: float | None = 0.8) -> CandidatePosition:
    return CandidatePosition(
        candidate_id="cand-test",
        position_name="RAG 工程师",
        state=state,
        features=DiscoveryFeatures(jd_freq_ma3=12.0, z_score=2.5, source_diversity=source_diversity),
        confidence=__import__("app.services.discovery.schemas", fromlist=["ConfidenceScore"]).ConfidenceScore(
            base_confidence=confidence, final_confidence=confidence or 0.0
        )
        if confidence is not None
        else None,
        detected_at="2026-08-02T00:00:00+08:00",
    )


class TestWindowHelpers:
    def test_volatility(self):
        assert window_volatility(WindowFreq([10, 8, 9])) == pytest.approx(0.2)
        assert window_volatility(WindowFreq([0, 0])) == 0.0

    def test_decline_rate(self):
        assert decline_rate(WindowFreq([10, 6, 5])) == pytest.approx(0.5)
        assert decline_rate(WindowFreq([10, 12])) == pytest.approx(-0.2)  # 上升为负
        assert decline_rate(WindowFreq([0, 0, 0])) == 0.0

    def test_recovery(self):
        assert has_recovery(WindowFreq([1, 2, 3], z_scores=[0.5, 0.8, 1.2])) is True
        assert has_recovery(WindowFreq([1, 2, 3], z_scores=[0.5, -0.1])) is False
        assert has_recovery(WindowFreq([1, 2], z_scores=[0.5])) is False


class TestAutoTransition:
    def test_emerging_to_stable(self):
        c = _candidate(PositionState.EMERGING, source_diversity=3, confidence=0.9)
        w = WindowFreq([10, 9, 10])
        assert evaluate_auto_transition(c, w) == PositionState.STABLE

    def test_emerging_stays_when_volatile(self):
        c = _candidate(PositionState.EMERGING, source_diversity=3, confidence=0.9)
        w = WindowFreq([10, 5, 10])  # 波动 50% > 25%
        assert evaluate_auto_transition(c, w) is None

    def test_emerging_to_declining(self):
        c = _candidate(PositionState.EMERGING, source_diversity=3, confidence=0.5)
        w = WindowFreq([10, 6, 5])
        assert evaluate_auto_transition(c, w) == PositionState.DECLINING

    def test_stable_to_declining(self):
        c = _candidate(PositionState.STABLE, source_diversity=2)
        w = WindowFreq([10, 6, 5])
        assert evaluate_auto_transition(c, w) == PositionState.DECLINING

    def test_stable_holds(self):
        c = _candidate(PositionState.STABLE, source_diversity=2)
        w = WindowFreq([10, 9, 10])
        assert evaluate_auto_transition(c, w) is None

    def test_declining_to_stable_on_recovery(self):
        c = _candidate(PositionState.DECLINING, source_diversity=2)
        w = WindowFreq([5, 6, 8], z_scores=[0.3, 0.7])
        assert evaluate_auto_transition(c, w) == PositionState.STABLE

    def test_candidate_never_auto_transitions(self):
        c = _candidate(PositionState.CANDIDATE)
        w = WindowFreq([10, 9, 10], z_scores=[0.5, 0.6])
        assert evaluate_auto_transition(c, w) is None


class TestPromoteToEmerging:
    def test_confidence_and_sources_ok(self):
        c = _candidate(PositionState.CANDIDATE, source_diversity=2, confidence=0.65)
        assert can_promote_to_emerging(c) is True

    def test_low_confidence(self):
        c = _candidate(PositionState.CANDIDATE, source_diversity=2, confidence=0.5)
        assert can_promote_to_emerging(c) is False

    def test_low_sources(self):
        c = _candidate(PositionState.CANDIDATE, source_diversity=1, confidence=0.9)
        assert can_promote_to_emerging(c) is False

    def test_explicit_confidence_overrides(self):
        c = _candidate(PositionState.CANDIDATE, source_diversity=3, confidence=0.3)
        assert can_promote_to_emerging(c, confidence=0.7) is True

    def test_no_confidence_score(self):
        c = _candidate(PositionState.CANDIDATE, source_diversity=3, confidence=None)
        assert can_promote_to_emerging(c) is False


class TestTransitionValidation:
    def setup_method(self):
        self.machine = PositionStateMachine()

    def test_valid_manual_transition(self):
        c = _candidate(PositionState.CANDIDATE)
        out = self.machine.transition(c, PositionState.EMERGING, operator="admin", reason="跨源验证通过")
        assert out.state == PositionState.EMERGING

    def test_invalid_transition(self):
        c = _candidate(PositionState.CANDIDATE)
        with pytest.raises(ValueError, match="非法状态转换"):
            self.machine.transition(c, PositionState.ARCHIVED, operator="system")

    def test_terminal_state_no_exit(self):
        c = _candidate(PositionState.ARCHIVED)
        with pytest.raises(ValueError):
            self.machine.transition(c, PositionState.STABLE, operator="system")

    def test_manual_requires_reason(self):
        c = _candidate(PositionState.CANDIDATE)
        with pytest.raises(ValueError, match="必须填写 reason"):
            self.machine.transition(c, PositionState.EMERGING, operator="admin")


class TestPersist:
    def setup_method(self):
        self.machine = PositionStateMachine()

    def test_persist_uses_merged_status(self):
        """persist 按 name MERGE 并 SET status，幂等且不要求节点已存在。"""
        executed = []

        class _FakeSession:
            def execute_write(self, fn):
                executed.append(fn)
                fn(_FakeTx())

        class _FakeTx:
            def run(self, query, **params):
                assert "MERGE (p:Position {name: $name})" in query
                assert "SET p.status = $state" in query
                assert params["state"] == "emerging"
                assert params["name"] == "RAG 工程师"

        c = _candidate(PositionState.CANDIDATE)
        out = self.machine.persist(
            _FakeSession(), c, PositionState.EMERGING, operator="admin", reason="审核通过",
        )
        assert out.state == PositionState.EMERGING
        assert len(executed) == 1
