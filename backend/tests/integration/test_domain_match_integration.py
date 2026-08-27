"""领域维度匹配单元测试（engine._domain_score / _domain_terms）。

方案 A（2026-08-27）：岗位画像改走 jd_raw 单条 JD，聚合 loader 移除后原
"真实图谱领域集成测试"失去岗位来源，改为对领域匹配纯函数做确定性单测
（不触 Neo4j / SBERT）。领域维度逻辑（_DOMAIN_ALIASES 词库归一化 +
DOMAIN_SEM_THRESHOLD=0.5 独立阈值）自 `engine._domain_score` 抽取，语义分支
用桩 embedder 注入，验证词面命中 / 语义兜底 / 无数据返 None 三通路。
"""

from __future__ import annotations

from app.services.matching.engine import _domain_score, _domain_terms
from app.services.matching.schemas import CandidateProfile, PositionProfile


class _FakeEmbedder:
    """语义桩：仅对预先配置的 (industry, domain) 对返回固定相似度。"""

    def __init__(self, sims: dict):
        self._sims = sims

    def similarity(self, a, b):
        return self._sims.get((a, b), 0.0)


def _candidate(domains: list[str]) -> CandidateProfile:
    return CandidateProfile(
        user_id="unit",
        skills=[],
        total_years=5,
        domain_experience=domains,
    )


def _position(industry: str) -> PositionProfile:
    return PositionProfile(
        position_id="p1", name="pos", industry=industry,
        must_skills=[], nice_skills=[],
    )


class TestDomainTermsNormalization:
    def test_composite_word_splits_into_atoms(self):
        """斜杠复合词拆为原子领域词（词库口径，aliases ∪ 原始拆分段 去重）。"""
        assert _domain_terms("SaaS/云技术") == ["云计算", "saas", "云技术"]
        # 已收录复合词返回 别名 ∪ 原始拆分段 的并集（去重）
        assert _domain_terms("电子商务") == ["电商", "电子商务"]

    def test_plain_industry_splits(self):
        assert _domain_terms("银行/金融服务") == ["银行", "金融服务"]


class TestDomainScore:
    def test_lexical_hit_returns_1_0(self):
        """词面命中：候选领域词与岗位行业原子词相等/子串 → 1.0。"""
        cand = _candidate(["电商"])
        pos = _position("电子商务")
        assert _domain_score(cand, pos) == 1.0

    def test_semantic_hit_above_threshold(self):
        """词面未命中的语义兜底：sim ≥ DOMAIN_SEM_THRESHOLD(0.5) → 计相似度值。"""
        cand = _candidate(["电商"])
        pos = _position("互联网")
        embedder = _FakeEmbedder({("互联网", "电商"): 0.52})
        assert _domain_score(cand, pos, semantic=embedder) == 0.52

    def test_semantic_below_threshold_returns_0(self):
        """语义低于阈值 → 0.0（不误判为命中）。"""
        cand = _candidate(["金融科技"])
        pos = _position("互联网")
        embedder = _FakeEmbedder({("互联网", "金融科技"): 0.3})
        assert _domain_score(cand, pos, semantic=embedder) == 0.0

    def test_no_data_returns_none(self):
        """岗位无行业或候选人无领域经验 → None（无信息不参与）。"""
        assert _domain_score(_candidate([]), _position("互联网")) is None
        assert _domain_score(_candidate(["电商"]), _position("")) is None

    def test_semantic_unavailable_returns_0(self):
        """无语义模型（None）：词面未命中 → 0.0，不阻断。"""
        cand = _candidate(["电商"])
        pos = _position("互联网")
        assert _domain_score(cand, pos, semantic=None) == 0.0
