"""技术热点观察池测试（设计文档 7.2.5）。

覆盖：
- MLI 计算：四维加权、0.6 阈值边界、单维命中
- 观察池信号判定：JD 环比（>50%）、2σ 偏离、数据不足不判定
- 信号聚合：按 (技能, 源) 分桶、snapshot 技能提取
"""

import pytest

from app.services.discovery.mli import MLI_WEIGHTS, compute_mli
from app.services.discovery.watch_pool import (
    WatchSignal,
    aggregate_weekly_freqs,
    build_signals,
    detect_jd_mom_signal,
    detect_z_signal,
    extract_skills,
    promotable_skills,
)


# ============================================================
# MLI
# ============================================================

class TestMLI:
    def test_all_threshold_ready(self):
        # 四维全部命中 → mli = 1.0 > 0.6
        result = compute_mli(z_paper=2.5, z_course=2.2, z_community=3.0, growth_jd=0.8)
        assert result.ready_to_industrialize
        assert result.mli == pytest.approx(1.0, abs=1e-4)

    def test_below_threshold_not_ready(self):
        # 仅论文一维命中 → mli = 0.25 < 0.6
        result = compute_mli(z_paper=2.5)
        assert not result.ready_to_industrialize
        assert result.mli == pytest.approx(0.25, abs=1e-4)

    def test_threshold_boundary(self):
        # mli 恰为 0.6（如 2 维命中 + 2 维 0.2）→ 严格 > 0.6 才 ready
        result = compute_mli(z_paper=2.0, z_course=2.0)  # 0.25 + 0.25 = 0.5
        assert result.mli == pytest.approx(0.5)
        assert not result.ready_to_industrialize

    def test_sub_threshold_normalization(self):
        # 未命中但非零 → value/threshold 截断
        result = compute_mli(z_paper=1.0, growth_jd=0.25)  # 0.5 + 0.5 = 1.0/2*0.25=...
        assert result.dimensions["paper"] == pytest.approx(0.5)
        assert result.dimensions["jd"] == pytest.approx(0.5)

    def test_weights_sum(self):
        assert sum(MLI_WEIGHTS.values()) == pytest.approx(1.0)
        assert set(MLI_WEIGHTS) == {"paper", "course", "community", "jd"}


# ============================================================
# 信号判定
# ============================================================

class TestSignalDetection:
    def test_z_signal_hit(self):
        # 最近周显著高于历史 → z > 2
        weekly = {"W1": 5, "W2": 6, "W3": 5, "W4": 30}
        z, hit = detect_z_signal(weekly)
        assert z > 2.0
        assert hit

    def test_z_signal_no_hit(self):
        weekly = {"W1": 5, "W2": 6, "W3": 5, "W4": 6}
        z, hit = detect_z_signal(weekly)
        assert not hit

    def test_z_insufficient_data(self):
        assert detect_z_signal({"W1": 5}) is None

    def test_jd_mom_hit(self):
        # 13 周（12 周窗口 + 1 个移动平均点），末 2 周暴增 → 环比 > 50%。
        # 周键用固定宽度 W01..W13（与真实 ISO 周键 YYYY-Www 同构，字典序=时间序）
        weekly = {f"W{i:02d}": 2 for i in range(1, 12)} | {"W12": 100, "W13": 100}
        growth, hit = detect_jd_mom_signal(weekly)
        assert growth > 0.5
        assert hit

    def test_jd_mom_no_hit(self):
        weekly = {f"W{i:02d}": 3 for i in range(1, 14)}
        assert detect_jd_mom_signal(weekly) is not None
        growth, hit = detect_jd_mom_signal(weekly)
        assert not hit

    def test_jd_insufficient_data(self):
        weekly = {f"W{i:02d}": 2 for i in range(1, 13)}  # 12 周 < 13（窗口+1）
        assert detect_jd_mom_signal(weekly) is None


# ============================================================
# 信号聚合
# ============================================================

class _Row:
    def __init__(self, source, snapshot, crawled_at):
        self.source = source
        self.snapshot = snapshot
        self.crawled_at = crawled_at


class TestAggregation:
    def test_extract_skills_course(self):
        assert extract_skills("coursera", {"skills": ["Python", "NLP"]}) == ["Python", "NLP"]
        assert extract_skills("coursera", {"skills": ["Python", 123]}) == ["Python"]

    def test_extract_skills_jd(self):
        snap = {"extraction": {"skills": [{"name": "Python"}, {"name": "PyTorch"}]}}
        assert extract_skills("boss", snap) == ["Python", "PyTorch"]

    def test_extract_skills_community(self):
        snap = {"language": "Python", "tags": ["machine-learning", "nlp"]}
        got = extract_skills("github", snap)
        assert "Python" in got and "machine-learning" in got

    def test_aggregate_by_skill_source(self):
        rows = [
            _Row("boss", {"extraction": {"skills": [{"name": "Python"}]}}, "2026-07-01"),
            _Row("github", {"language": "Python"}, "2026-07-01"),
        ]
        freqs = aggregate_weekly_freqs(rows)
        assert ("Python", "boss") in freqs
        assert ("Python", "github") in freqs
        assert len(freqs) == 2  # 同技能不同源独立分桶

    def test_build_signals_jd_source(self):
        # jd 源：末 2 周暴增 → 环比命中
        weekly = {f"W{i:02d}": 2 for i in range(1, 12)} | {"W12": 100, "W13": 100}
        freqs = {("PyTorch", "jd"): weekly}
        signals = build_signals(freqs, "2026-08-05")
        assert len(signals) == 1
        assert signals[0].signal_source == "jd"
        assert signals[0].skill_name == "PyTorch"

    def test_build_signals_z_source(self):
        weekly = {"W1": 5, "W2": 6, "W3": 5, "W4": 30}
        signals = build_signals({("Rust", "arxiv"): weekly}, "2026-08-05")
        assert len(signals) == 1
        assert signals[0].signal_source == "arxiv"

    def test_build_signals_no_hit(self):
        weekly = {"W1": 5, "W2": 6, "W3": 5, "W4": 6}
        assert build_signals({("Go", "github"): weekly}, "2026-08-05") == []


# ============================================================
# 观察池提升判定（设计 §7.2.5 / 方案 §2：JD 命中 + 此前在池）
# ============================================================

class TestPromotableSkills:
    def test_jd_hit_with_prior_history_promotes(self):
        signals = [
            WatchSignal("Rust", "arxiv", 2.5, "2026-08-05"),
            WatchSignal("Rust", "jd", 0.8, "2026-08-05"),
            WatchSignal("Go", "jd", 0.7, "2026-08-05"),
        ]
        # 仅 Rust 有此前观察历史 → 只提升 Rust
        assert promotable_skills(signals, {"Rust"}) == ["Rust"]

    def test_jd_first_hit_without_history_not_promoted(self):
        signals = [WatchSignal("WebAssembly", "jd", 0.9, "2026-08-05")]
        assert promotable_skills(signals, set()) == []

    def test_non_jd_signals_never_promote(self):
        signals = [
            WatchSignal("Rust", "arxiv", 2.5, "2026-08-05"),
            WatchSignal("Rust", "course", 2.2, "2026-08-05"),
        ]
        assert promotable_skills(signals, {"Rust"}) == []  # 无 JD 信号

    def test_dedup_and_order(self):
        signals = [
            WatchSignal("Rust", "jd", 0.8, "2026-08-05"),
            WatchSignal("Rust", "github", 2.5, "2026-08-05"),
            WatchSignal("Rust", "jd", 0.9, "2026-08-05"),  # 重复 JD 信号
            WatchSignal("PyTorch", "jd", 0.6, "2026-08-05"),
        ]
        assert promotable_skills(signals, {"Rust", "PyTorch"}) == ["Rust", "PyTorch"]
