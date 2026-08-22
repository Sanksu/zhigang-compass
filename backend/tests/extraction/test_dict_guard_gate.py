"""dict-guard 评估服务纯逻辑测试：硬门禁矩阵 / 分级矩阵 / 候选筛选。

不触 DB 与 LLM；分级阈值经 monkeypatch runtime_config.get 控制。
"""

import pytest
from app.core import runtime_config
from app.services.extraction.dict_guard import (
    _ALIAS_STANDARDS,
    hard_gate,
    select_stopword_misuse,
    select_suspect_skills,
    tier_for,
)
from app.services.extraction.dictionary import SKILL_STOPWORDS, SKILL_WHITELIST


def _pure_stopword() -> str:
    """确定性取「纯」停用词样本（≥2 字符且不与白名单/别名标准名重叠）。

    SKILL_STOPWORDS 含单字符「微」（硬门禁按词条过短拒绝）与白名单重叠
    成员（如「缓存」「数据库」，protect 场景按已受保护拒绝）；set 迭代序
    随 PYTHONHASHSEED 漂移，next(iter(...)) 取样在 CI 偶发抽中致断言失败。
    """
    return sorted(
        w
        for w in SKILL_STOPWORDS
        if len(w) >= 2 and w not in SKILL_WHITELIST and w not in _ALIAS_STANDARDS
    )[0]


class TestHardGate:
    def test_add_stopword_accepts_long_tail_noise(self):
        ok, reason = hard_gate("add_stopword", "数字化转型的")
        assert ok is True

    def test_add_stopword_vetoes_whitelist(self):
        # 核心不变量：停用词优先于白名单，白名单词绝不可入停用词（误加即误杀）
        sample = next(iter(SKILL_WHITELIST))
        ok, reason = hard_gate("add_stopword", sample)
        assert ok is False
        assert "白名单" in reason

    def test_add_stopword_vetoes_existing_stopword(self):
        sample = next(iter(SKILL_STOPWORDS))
        ok, _ = hard_gate("add_stopword", sample)
        assert ok is False

    def test_add_stopword_vetoes_short_term(self):
        ok, reason = hard_gate("add_stopword", "微")
        assert ok is False
        assert "过短" in reason

    def test_remove_stopword_requires_current_stopword(self):
        sample = _pure_stopword()
        assert hard_gate("remove_stopword", sample)[0] is True
        assert hard_gate("remove_stopword", "不是停用词的词")[0] is False

    def test_protect_requires_blocked_target(self):
        sample = _pure_stopword()
        assert hard_gate("protect_whitelist", sample)[0] is True
        whitelist_sample = next(iter(SKILL_WHITELIST))
        assert hard_gate("protect_whitelist", whitelist_sample)[0] is False
        assert hard_gate("protect_whitelist", "未被拦截的词")[0] is False

    def test_unknown_action_rejected(self):
        assert hard_gate("delete_everything", "某词")[0] is False


class TestTierFor:
    @pytest.fixture(autouse=True)
    def _default_config(self, monkeypatch):
        monkeypatch.setattr(
            runtime_config, "get",
            lambda k, d=None: {
                "dict_guard_auto_impact_threshold": 50,
                "dict_guard_min_confidence": 0.8,
            }.get(k, d),
        )

    def test_gate_fail_skips(self):
        assert tier_for("add_stopword", False, 0, 0.99) == "skip"

    def test_remove_and_protect_always_proposal(self):
        assert tier_for("remove_stopword", True, 0, 0.99) == "proposal"
        assert tier_for("protect_whitelist", True, 0, 0.99) == "proposal"

    def test_low_risk_add_is_auto(self):
        assert tier_for("add_stopword", True, 10, 0.9) == "auto"

    def test_high_impact_demotes_to_proposal(self):
        assert tier_for("add_stopword", True, 100, 0.9) == "proposal"

    def test_low_confidence_demotes_to_proposal(self):
        assert tier_for("add_stopword", True, 10, 0.5) == "proposal"


class TestSelectSuspectSkills:
    def test_filters_protected_and_keeps_long_tail(self):
        rows = [
            {"name": next(iter(SKILL_WHITELIST)), "first_seen": "2026-08-01", "category": "编程语言", "req_count": 0},
            {"name": next(iter(SKILL_STOPWORDS)), "first_seen": "2026-08-01", "category": "", "req_count": 1},
            {"name": "x", "first_seen": "2026-08-01", "category": "", "req_count": 0},  # 过短
            {"name": "某小众框架", "first_seen": "2026-08-20", "category": None, "req_count": 1},
        ]
        suspects = select_suspect_skills(rows)
        assert [s["term"] for s in suspects] == ["某小众框架"]
        assert suspects[0]["evidence"]["图谱引用数(REQUIRES)"] == 1


class TestSelectStopwordMisuse:
    def test_collision_emits_candidate(self):
        corpus = "岗位要求：熟悉自动化测试与持续集成。自动化测试经验优先。"
        misuses = select_stopword_misuse(corpus, {"测试"}, {"自动化测试"})
        assert len(misuses) == 1
        assert misuses[0]["term"] == "测试"
        assert misuses[0]["kind"] == "stopword_misuse"
        assert misuses[0]["evidence"]["受影响技能"] == "自动化测试"

    def test_victim_absent_from_corpus_no_candidate(self):
        misuses = select_stopword_misuse("无关内容", {"测试"}, {"自动化测试"})
        assert misuses == []

    def test_short_stopword_skipped(self):
        misuses = select_stopword_misuse("自动化测试", {"测"}, {"自动化测试"})
        assert misuses == []
