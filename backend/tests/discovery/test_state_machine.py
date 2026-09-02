"""岗位状态机单元测试（设计文档 7.2.1 节）。

覆盖：转换合法性校验、自动转换判定（stable/declining/recovery）、
candidate→emerging 条件、持久化幂等与审计日志。
"""

import pytest
from tests.helpers import SeqResult

from app.services.discovery.schemas import CandidatePosition, DiscoveryFeatures, PositionState
from app.services.discovery.state_machine import (
    WindowFreq,
    can_promote_to_emerging,
    decline_rate,
    evaluate_auto_transition,
    evaluate_active_decline,
    freq_z_scores,
    has_recovery,
    jd_publish_windows,
    position_freq_windows,
    window_volatility,
    PositionStateMachine,
)




def _candidate(
    state: PositionState,
    source_diversity: int = 2,
    confidence: float | None = 0.8,
    jd_count: int = 5,
) -> CandidatePosition:
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
        evidence_refs=[f"ev_{i}" for i in range(jd_count)],
    )


class TestWindowHelpers:
    def test_volatility(self):
        # [10,8,9] 最近 2 窗口为 [8,9]：末窗增长不构成波动 → 0.0
        # （08-19 口径修正：波动=萎缩幅度 (prev-last)/prev，增长/首采接入不算不稳定）
        assert window_volatility(WindowFreq([10, 8, 9])) == 0.0
        assert window_volatility(WindowFreq([0, 0])) == 0.0
        # 萎缩保留：末窗相对前一窗口的下降比例
        assert window_volatility(WindowFreq([10, 9, 6])) == pytest.approx(0.3333, abs=1e-3)

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
        [:2]=[10,8] 波动 0.2，[-2:]=[9,1] 波动 0.889——最近窗口剧烈萎缩
        才应触发降级判定。
        """
        assert window_volatility(WindowFreq([10, 8, 9, 1])) == pytest.approx(0.8889, abs=1e-3)

    def test_decline_rate_uses_recent_windows(self):
        """下降率取最近 n 窗口：早期窗口上升、最近窗口骤降必须被捕捉。

        回归：freqs=[10,12,10,4] n=3 时 [:3]=[10,12,10] 下降率 0，
        [-3:]=[12,10,4] 下降率 0.667。
        """
        assert decline_rate(WindowFreq([10, 12, 10, 4])) == pytest.approx(0.6667, abs=1e-3)


class TestJdPublishWindows:
    """从 jd_raw 按发布日聚合的岗位频次窗口序列（declining 信号源，30 天窗口）。

    窗口以全部日期最晚日为终点对齐（end 由数据推断），序列时间升序；
    岗位窗口未覆盖处补 0（近期无发布 → 序列尾部 0，即下降信号）。
    """

    def test_windows_aligned_to_latest_date(self):
        # end=2026-08-11（数据最晚日），窗口 0=(07-13..08-11]、
        # 窗口1=(06-13..07-12]、窗口2=(05-14..06-12]；各日落入其窗口，升序输出
        daily = {"岗位A": {"2026-08-11": 5, "2026-07-12": 3, "2026-06-12": 2}}
        assert jd_publish_windows(daily) == {"岗位A": [2.0, 3.0, 5.0]}

    def test_empty_returns_empty(self):
        assert jd_publish_windows({}) == {}

    def test_gap_padded_with_zeros(self):
        # 窗口 0 与窗口 3 有发布（窗口 1/2 无记录补 0）：序列尾部 0 即
        # "近期无发布"的下降信号（declining 判定依赖此补 0 语义）
        daily = {"岗位A": {"2026-08-11": 5, "2026-05-12": 2}}
        assert jd_publish_windows(daily) == {"岗位A": [2.0, 0.0, 0.0, 5.0]}

    def test_daily_counts_aggregated_into_window(self):
        # 同一窗口内多日发布数求和
        daily = {"岗位A": {"2026-08-11": 3, "2026-08-10": 2, "2026-07-12": 4}}
        assert jd_publish_windows(daily) == {"岗位A": [4.0, 5.0]}

    def test_custom_window_size(self):
        # 7 天窗口：08-11 与 08-10 同窗口（间隔 1 天），08-04 落在上一窗口
        daily = {"岗位A": {"2026-08-11": 3, "2026-08-10": 1, "2026-08-04": 2}}
        assert jd_publish_windows(daily, window_days=7) == {"岗位A": [2.0, 4.0]}


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

    def test_non_requires_edges_not_counted(self):
        """P2 频次口径修复：仅 REQUIRES 出边计入岗位频次。
        HAS_EVIDENCE/BELONGS_TO_OCCUPATION 等维护边不再虚增频次
        （修复前计全部出边，岗位频次被证据/归属边污染）。"""
        snap = {
            "nodes": [{"id": "pos_x", "name": "Java 开发工程师", "type": "position"}],
            "edges": [
                {"source": "pos_x", "target": "sk_1", "relation": "REQUIRES"},
                {"source": "pos_x", "target": "sk_2", "relation": "REQUIRES"},
                {"source": "pos_x", "target": "ev_1", "relation": "HAS_EVIDENCE"},
                {"source": "pos_x", "target": "occ_1", "relation": "BELONGS_TO_OCCUPATION"},
            ],
        }
        out = position_freq_windows([snap], {"Java 开发工程师"})
        assert out["Java 开发工程师"] == [2.0]

    def test_legacy_edges_without_relation_still_counted(self):
        """旧快照 edges 无 relation 字段（relation 导出前的版本）按 REQUIRES
        处理，历史窗口序列不因字段缺失而整体清零。"""
        snap = {
            "nodes": [{"id": "pos_x", "name": "Java 开发工程师", "type": "position"}],
            "edges": [
                {"source": "pos_x", "target": "sk_1"},
                {"source": "pos_x", "target": "sk_2"},
            ],
        }
        out = position_freq_windows([snap], {"Java 开发工程师"})
        assert out["Java 开发工程师"] == [2.0]


class TestAutoTransition:
    def test_emerging_to_stable(self):
        c = _candidate(PositionState.EMERGING, source_diversity=3, confidence=0.9)
        w = WindowFreq([10, 9, 10])
        assert evaluate_auto_transition(c, w) == PositionState.STABLE

    def test_emerging_stays_when_jd_count_below_5(self):
        """§7.2.1 对齐（08-15）：jd_count < 5 不升级 stable（小基数保护）。

        回归：原实现用 confidence ≥ 0.8 替代 jd_count 门槛——jd_count=4 时
        其他维度满分也能过 0.8 提前稳定。
        """
        c = _candidate(PositionState.EMERGING, source_diversity=3, confidence=0.9, jd_count=4)
        w = WindowFreq([10, 9, 10])
        assert evaluate_auto_transition(c, w) is None

    def test_emerging_to_stable_at_jd_count_boundary(self):
        """jd_count = 5 恰好达标（边界）。"""
        c = _candidate(PositionState.EMERGING, source_diversity=3, jd_count=5)
        w = WindowFreq([10, 9, 10])
        assert evaluate_auto_transition(c, w) == PositionState.STABLE

    def test_emerging_stays_when_skill_novelty_high(self):
        """§7.2.1：skill_novelty ≥ 0.2 不升级 stable（新技能驱动岗位仍处演化期）。"""
        c = _candidate(PositionState.EMERGING, source_diversity=3, jd_count=5)
        w = WindowFreq([10, 9, 10])
        assert evaluate_auto_transition(c, w, skill_novelty=0.5) is None
        # 边界：0.2 不满足（< 0.2 严格，08-15 需求调整）
        assert evaluate_auto_transition(c, w, skill_novelty=0.2) is None
        # 0.19 达标
        assert evaluate_auto_transition(c, w, skill_novelty=0.19) == PositionState.STABLE

    def test_emerging_to_stable_when_novelty_none(self):
        """skill_novelty=None（数据不可得）不拦截——保持既有行为。"""
        c = _candidate(PositionState.EMERGING, source_diversity=3, jd_count=5)
        w = WindowFreq([10, 9, 10])
        assert evaluate_auto_transition(c, w, skill_novelty=None) == PositionState.STABLE

    def test_emerging_stays_when_shrinking(self):
        """最近窗口相对前一窗口萎缩 >25% → 不晋 stable（波动护栏保留）。"""
        c = _candidate(PositionState.EMERGING, source_diversity=3, confidence=0.9)
        w = WindowFreq([10, 9, 6])  # 最近 2 窗口 [9,6] 萎缩 33% > 25%
        assert evaluate_auto_transition(c, w) is None

    def test_emerging_promoted_on_recent_growth(self):
        """首采接入导致的增长不算波动 → 照常晋级 stable（08-19 口径修正）。"""
        c = _candidate(PositionState.EMERGING, source_diversity=3, confidence=0.9)
        w = WindowFreq([10, 5, 10])  # 最近 2 窗口 [5,10] 增长（旧对称波动 50% 曾误拦）
        assert evaluate_auto_transition(c, w) == PositionState.STABLE

    def test_emerging_to_declining(self):
        """下降趋势 + 最近窗口仍显著萎缩 → declining（萎缩 37.5% > 25% 不进 stable）。"""
        c = _candidate(PositionState.EMERGING, source_diversity=3, confidence=0.5)
        w = WindowFreq([10, 8, 5])  # 最近 2 窗口 [8,5] 萎缩 37.5%，decline 50%
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
        """3 期快照最近窗口显著萎缩（>25%）→ 不升级 stable。"""
        name = "RAG 工程师"
        snaps = [
            self._snapshot(name, 10),
            self._snapshot(name, 8),
            self._snapshot(name, 4),
        ]
        windows = position_freq_windows(snaps, {name})
        c = _candidate(PositionState.EMERGING, source_diversity=3, confidence=0.9)
        target = evaluate_auto_transition(c, WindowFreq(freqs=windows[name]))
        assert target != PositionState.STABLE

    def test_emerging_to_stable_on_cold_start_growth(self):
        """首采接入的增长型（后窗远大于前窗）应能晋级 stable（08-19 口径修正）。

        回归：旧对称 (max-min)/max 把 08 首采批次导致的末窗爆发判为波动
        ≈100%，使 25 个 emerging 全部无法晋级；新口径只惩罚萎缩。
        """
        name = "RAG 工程师"
        snaps = [
            self._snapshot(name, 2),
            self._snapshot(name, 6),
            self._snapshot(name, 50),
        ]
        windows = position_freq_windows(snaps, {name})
        c = _candidate(PositionState.EMERGING, source_diversity=3, jd_count=8, confidence=0.9)
        assert evaluate_auto_transition(c, WindowFreq(freqs=windows[name])) == PositionState.STABLE

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
                # next_id 先发 Counter 自增查询（08-14：创建时补全 id/freq）
                if "Counter" in query:
                    return SeqResult(7)
                assert "MERGE (p:Position {name: $name})" in query
                assert "ON CREATE SET p.id = $pid, p.freq = 0" in query
                assert params["pid"] == "pos_0007"
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

    def test_confidence_055_boundary(self):
        """置信度门槛 0.6→0.55（P1，2026-09-02）：恰好 0.55 可晋升。

        背景：观测期首现岗的 growth 维天然归零（快照末尾平稳），base =
        0.4×norm(ma3) + 0.3×norm(src) 恒卡 0.55——改 growth 窗口仅救回有上升
        脉冲的岗位，对一入池即高位平稳者无效。此类岗位已由存量排除 + 跨源≥2
        双重防线把关，0.55 是这批岗位的真实分布下限，放宽一格可放行。
        """
        c = _candidate(PositionState.CANDIDATE, source_diversity=2, confidence=0.55)
        assert can_promote_to_emerging(c) is True
        # 稍低于 0.55 仍拦截
        c_below = _candidate(PositionState.CANDIDATE, source_diversity=2, confidence=0.54)
        assert can_promote_to_emerging(c_below) is False


class TestActiveDecline:
    """图谱存量聚合岗衰退判定（方案 A，2026-09-02）。

    evaluate_active_decline 为图谱 active/legacy（无候选池行）岗位的专属衰退判定。
    与状态机 evaluate_auto_transition 不同：存量岗无候选池证据锚点，仅凭
    jd_publish_windows 补 0 序列会误判——必须靠 jd_count>=5 + 源>=2 + 窗口>=2
    三道门控排除"单条观测期外旧 JD、末窗补 0"的伪影（实测 7 个 dr=1.0 全此类）。
    """

    def test_real_decline_detected(self):
        """连续 3 窗口下降 >40% + 证据充分 → declining。"""
        w = WindowFreq([10.0, 8.0, 5.0])  # 下降率 (10-5)/10 = 50% > 40%
        assert evaluate_active_decline(w, jd_count=8, source_diversity=3) == PositionState.DECLINING

    def test_single_jd_artifact_not_detected(self):
        """伪影岗：仅 1 条 JD、末窗补 0，dr=1.0 但证据量不足 → 不判衰退。"""
        w = WindowFreq([1.0, 0.0])  # 补 0 伪影，dr=1.0
        # jd_count=1 < 5：防伪影门控拦住，不误标
        assert evaluate_active_decline(w, jd_count=1, source_diversity=2) is None

    def test_insufficient_windows_not_detected(self):
        """窗口序列 <2 期 → 数据不足不武断判定。"""
        w = WindowFreq([5.0])
        assert evaluate_active_decline(w, jd_count=8, source_diversity=3) is None

    def test_single_source_not_detected(self):
        """源多样性 <2 → 单源低证据存量岗不判衰退。"""
        w = WindowFreq([10.0, 6.0, 5.0])
        assert evaluate_active_decline(w, jd_count=8, source_diversity=1) is None

    def test_stable_going_up_not_detected(self):
        """存量岗频次上升 → decline_rate 为负，不判衰退。"""
        w = WindowFreq([5.0, 8.0, 12.0])  # 上升
        assert evaluate_active_decline(w, jd_count=10, source_diversity=3) is None

    def test_boundary_40_percent_detected(self):
        """下降率恰好略超 40% → declining（边界）。"""
        w = WindowFreq([10.0, 10.0, 5.0])  # (10-5)/10 = 50% > 40%
        assert evaluate_active_decline(w, jd_count=6, source_diversity=2) == PositionState.DECLINING

    def test_below_40_percent_not_detected(self):
        """下降率低于 40% → 不判衰退（未到阈值）。"""
        w = WindowFreq([10.0, 9.0, 7.0])  # (10-7)/10 = 30% < 40%
        assert evaluate_active_decline(w, jd_count=8, source_diversity=3) is None


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
                if "Counter" in query:
                    return SeqResult(1)
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


class TestMixedSnapshotRelationCaliber:
    """混布快照 relation 口径（第六轮审查算法口径 3，zkt 复核）。

    快照级 any() 判定与 evolution/_requires_edges 一致：
    - 任一边带 relation 标注 → 无标注边不计入（旧逐边默认 REQUIRES 会计入）
    - 全部无标注（旧快照）→ 整期按 REQUIRES 处理（历史序列连续）
    - 非 REQUIRES 标注边一律不计入
    """

    @staticmethod
    def _mixed_snapshot() -> dict:
        """2 条 REQUIRES + 1 条 BELONGS_TO + 1 条无标注。"""
        return {
            "nodes": [{"id": "pos_后端", "name": "后端工程师", "type": "position"}],
            "edges": [
                {"source": "pos_后端", "target": "sk_1", "relation": "REQUIRES"},
                {"source": "pos_后端", "target": "sk_2", "relation": "REQUIRES"},
                {"source": "pos_后端", "target": "sk_3", "relation": "BELONGS_TO"},
                {"source": "pos_后端", "target": "sk_4"},  # 混布：无标注
            ],
        }

    def test_mixed_snapshot_excludes_unannotated_edges(self):
        out = position_freq_windows([self._mixed_snapshot()], {"后端工程师"})
        assert out["后端工程师"] == [2.0]  # 仅 2 条 REQUIRES；无标注边不计入

    def test_legacy_unannotated_snapshot_counts_all(self):
        snap = {
            "nodes": [{"id": "pos_后端", "name": "后端工程师", "type": "position"}],
            "edges": [
                {"source": "pos_后端", "target": "sk_1"},
                {"source": "pos_后端", "target": "sk_2"},
            ],
        }
        out = position_freq_windows([snap], {"后端工程师"})
        assert out["后端工程师"] == [2.0]  # 旧快照全量计入（序列连续）

    def test_fully_annotated_snapshot_ignores_maintenance_edges(self):
        snap = {
            "nodes": [{"id": "pos_后端", "name": "后端工程师", "type": "position"}],
            "edges": [
                {"source": "pos_后端", "target": "sk_1", "relation": "REQUIRES"},
                {"source": "pos_后端", "target": "sk_2", "relation": "HAS_EVIDENCE"},
            ],
        }
        out = position_freq_windows([snap], {"后端工程师"})
        assert out["后端工程师"] == [1.0]
