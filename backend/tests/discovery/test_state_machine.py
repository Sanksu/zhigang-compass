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
    freq_z_scores,
    has_recovery,
    position_freq_windows,
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
        # [10,8,9] 最近 2 窗口为 [8,9] 波动 0.111（原断言 0.2 依赖 freqs[:n]
        # 取最早窗口的 bug，已随窗口方向修复更正）
        assert window_volatility(WindowFreq([10, 8, 9])) == pytest.approx(0.1111, abs=1e-3)
        assert window_volatility(WindowFreq([0, 0])) == 0.0

    def test_decline_rate(self):
        assert decline_rate(WindowFreq([10, 6, 5])) == pytest.approx(0.5)
        assert decline_rate(WindowFreq([10, 12])) == pytest.approx(-0.2)  # 上升为负
        assert decline_rate(WindowFreq([0, 0, 0])) == 0.0

    def test_recovery(self):
        assert has_recovery(WindowFreq([1, 2, 3], z_scores=[0.5, 0.8, 1.2])) is True
        assert has_recovery(WindowFreq([1, 2, 3], z_scores=[0.5, -0.1])) is False
        assert has_recovery(WindowFreq([1, 2], z_scores=[0.5])) is False

    def test_recovery_checks_recent_windows(self):
        """最近 2 窗口回升才算回迁；早期窗口为负不影响判定。"""
        assert has_recovery(WindowFreq([1, 2, 3], z_scores=[-1.5, 0.5, 0.8])) is True
        assert has_recovery(WindowFreq([1, 2, 3], z_scores=[0.5, 0.8, -0.2])) is False

    def test_volatility_uses_recent_windows(self):
        """波动率取最近 n 窗口（非最早 n 窗口）。

        回归：原实现 freqs[:n] 取最早窗口，freqs=[10,8,9,1] n=2 时
        [:2]=[10,8] 波动 0.2，[-2:]=[9,1] 波动 0.889——最近窗口剧烈波动
        才应触发降级判定。
        """
        assert window_volatility(WindowFreq([10, 8, 9, 1])) == pytest.approx(0.8889, abs=1e-3)

    def test_decline_rate_uses_recent_windows(self):
        """下降率取最近 n 窗口：早期窗口上升、最近窗口骤降必须被捕捉。

        回归：freqs=[10,12,10,4] n=3 时 [:3]=[10,12,10] 下降率 0，
        [-3:]=[12,10,4] 下降率 0.667。
        """
        assert decline_rate(WindowFreq([10, 12, 10, 4])) == pytest.approx(0.6667, abs=1e-3)


class TestFreqZScores:
    """频次序列逐窗口 Z-score（declining → stable 回迁判定输入）。"""

    def test_basic_math(self):
        zs = freq_z_scores([5, 6, 8])
        # mean=19/3, std≈1.247
        assert zs == pytest.approx([-1.069, -0.267, 1.336], abs=1e-3)

    def test_empty(self):
        assert freq_z_scores([]) == []

    def test_constant_series_has_no_signal(self):
        """标准差为 0（全相等）时各窗口 z 取 0，不触发回迁。"""
        assert freq_z_scores([8, 8, 8]) == [0.0, 0.0, 0.0]


class TestPositionFreqWindows:
    """从图谱版本快照重建岗位频次窗口序列（自动流转数据源）。"""

    @staticmethod
    def _snapshot(position_edges: dict[str, list[str]]) -> dict:
        """构造快照：position_edges 为 {岗位名: [关联边源 id 列表]}。

        边以岗位 id 为 source、技能 id（sk_xxx）为 target 计数。
        """
        nodes = [{"id": f"pos_{name}", "name": name, "type": "position"} for name in position_edges]
        edges = []
        for name, targets in position_edges.items():
            for i, t in enumerate(targets):
                edges.append({"source": f"pos_{name}", "target": t})
        return {"nodes": nodes, "edges": edges}

    def test_sequence_built_in_time_order(self):
        """跨快照频次序列按时间升序（第一期在前）。"""
        snap1 = self._snapshot({"Java 开发工程师": ["sk_1", "sk_2", "sk_3"]})
        snap2 = self._snapshot({"Java 开发工程师": ["sk_1", "sk_2"]})
        out = position_freq_windows([snap1, snap2], {"Java 开发工程师"})
        assert out["Java 开发工程师"] == [3.0, 2.0]

    def test_ignores_unrelated_positions(self):
        """不在 position_names 中的岗位不参与构建。"""
        snap = self._snapshot({"Java 开发工程师": ["sk_1"], "无关岗位": ["sk_9"]})
        out = position_freq_windows([snap], {"Java 开发工程师"})
        assert "无关岗位" not in out
        assert out["Java 开发工程师"] == [1.0]

    def test_merged_same_name_sums_windows(self):
        """同名岗位多 id 时频次逐窗口求和（归一化合并口径）。"""
        snap = {
            "nodes": [
                {"id": "pos_a", "name": "软件开发工程师", "type": "position"},
                {"id": "pos_b", "name": "软件开发工程师", "type": "position"},
            ],
            "edges": [
                {"source": "pos_a", "target": "sk_1"},
                {"source": "pos_b", "target": "sk_2"},
                {"source": "pos_b", "target": "sk_3"},
            ],
        }
        out = position_freq_windows([snap], {"软件开发工程师"})
        assert out["软件开发工程师"] == [3.0]

    def test_empty_snapshots(self):
        assert position_freq_windows([], {"Java 开发工程师"}) == {}

    def test_position_without_edges_padded_with_zeros(self):
        """岗位某期无关联边 → 该期补 0，序列与快照窗口数等长对齐。"""
        snap = {
            "nodes": [{"id": "pos_x", "name": "孤岗", "type": "position"}],
            "edges": [],
        }
        out = position_freq_windows([snap, snap], {"孤岗"})
        assert out["孤岗"] == [0.0, 0.0]


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


class TestAutoTransitionFromSnapshots:
    """从 3 期以上图谱快照重建窗口序列，验证 emerging→stable 自动流转全链路。

    对齐 discovery_auto_transition 任务的数据链：
    graph_versions 快照序列 → position_freq_windows → evaluate_auto_transition。
    """

    @staticmethod
    def _snapshot(name: str, edge_count: int) -> dict:
        return TestPositionFreqWindows._snapshot(
            {name: [f"sk_{i}" for i in range(edge_count)]}
        )

    def test_emerging_to_stable_across_four_windows(self):
        """4 期快照频次平稳（波动 < 25%）+ 高置信 → emerging 自动升级 stable。"""
        name = "RAG 工程师"
        snaps = [
            self._snapshot(name, 10),
            self._snapshot(name, 11),
            self._snapshot(name, 10),
            self._snapshot(name, 11),
        ]
        windows = position_freq_windows(snaps, {name})
        assert windows[name] == [10.0, 11.0, 10.0, 11.0]

        c = _candidate(PositionState.EMERGING, source_diversity=3, confidence=0.9)
        target = evaluate_auto_transition(c, WindowFreq(freqs=windows[name]))
        assert target == PositionState.STABLE

    def test_single_window_reaches_stable_without_task_gate(self):
        """单期序列波动 0 会判定 STABLE——因此任务层必须保留快照 < 2 期闸门。

        discovery_auto_transition 在 len(snapshots) < 2 时直接返回（冷启动不武断），
        该闸门在任务层而非判定层，此处验证判定层对单期序列的固有行为。
        """
        name = "RAG 工程师"
        snaps = [self._snapshot(name, 10)]
        windows = position_freq_windows(snaps, {name})
        assert windows[name] == [10.0]

        c = _candidate(PositionState.EMERGING, source_diversity=3, confidence=0.9)
        # 单期波动 (max-min)/max = 0 < 25%，判定层会升级；任务层闸门负责拦截
        assert evaluate_auto_transition(c, WindowFreq(freqs=windows[name])) == PositionState.STABLE

    def test_emerging_not_promoted_when_window_unstable(self):
        """3 期快照波动大（> 25%）→ 不升级。"""
        name = "RAG 工程师"
        snaps = [
            self._snapshot(name, 10),
            self._snapshot(name, 6),
            self._snapshot(name, 10),
        ]
        windows = position_freq_windows(snaps, {name})
        c = _candidate(PositionState.EMERGING, source_diversity=3, confidence=0.9)
        target = evaluate_auto_transition(c, WindowFreq(freqs=windows[name]))
        assert target is None

    def test_declining_to_stable_recovery_across_snapshots(self):
        """4 期快照频次先降后升（最近 2 窗口 z > 0）→ declining 自动回迁 stable。

        覆盖 T-01 修复：z_scores 由频次序列重建后传入，回迁判定不再失效。
        """
        name = "RAG 工程师"
        snaps = [
            self._snapshot(name, 10),
            self._snapshot(name, 4),
            self._snapshot(name, 8),
            self._snapshot(name, 8),
        ]
        windows = position_freq_windows(snaps, {name})
        c = _candidate(PositionState.DECLINING, source_diversity=2)
        target = evaluate_auto_transition(
            c,
            WindowFreq(freqs=windows[name], z_scores=freq_z_scores(windows[name])),
        )
        assert target == PositionState.STABLE

    def test_declining_holds_when_no_recovery(self):
        """频次持续下降（最近 2 窗口 z ≤ 0）→ 不回迁，保持 declining。"""
        name = "RAG 工程师"
        snaps = [
            self._snapshot(name, 10),
            self._snapshot(name, 6),
            self._snapshot(name, 5),
        ]
        windows = position_freq_windows(snaps, {name})
        c = _candidate(PositionState.DECLINING, source_diversity=2)
        target = evaluate_auto_transition(
            c,
            WindowFreq(freqs=windows[name], z_scores=freq_z_scores(windows[name])),
        )
        assert target is None

    def test_stable_persist_idempotent_after_promotion(self):
        """emerging→stable 判定后 persist 幂等：重复执行产生相同 MERGE。"""
        name = "RAG 工程师"
        snaps = [self._snapshot(name, 10), self._snapshot(name, 11), self._snapshot(name, 11)]
        windows = position_freq_windows(snaps, {name})
        c = _candidate(PositionState.EMERGING, source_diversity=3, confidence=0.9)
        target = evaluate_auto_transition(c, WindowFreq(freqs=windows[name]))
        assert target == PositionState.STABLE

        machine = PositionStateMachine()
        executed = []

        class _FakeSession:
            def execute_write(self, fn):
                executed.append(fn)
                fn(_FakeTx())

        class _FakeTx:
            def run(self, query, **params):
                assert "MERGE (p:Position {name: $name})" in query
                assert params["state"] == "stable"
                assert params["name"] == name

        out = machine.persist(_FakeSession(), c, target, operator="system")
        assert out.state == PositionState.STABLE
        assert len(executed) == 1


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
