"""dict-guard 评估服务纯逻辑测试：硬门禁矩阵 / 分级矩阵 / 候选筛选。

不触 DB 与 LLM；分级阈值经 monkeypatch runtime_config.get 控制。
"""

import pytest
from app.core import runtime_config
from app.services.extraction.dict_guard import (
    _ALIAS_STANDARDS,
    DictGuardDecision,
    hard_gate,
    select_dirty_course_edges,
    select_dirty_positions,
    select_isolated_courses,
    select_stopword_misuse,
    select_suspect_skills,
    tier_for,
)
from app.services.extraction.dictionary import SKILL_STOPWORDS, SKILL_WHITELIST, _POSITION_WHITELIST


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


class TestDecisionSchema:
    def test_defaults_to_skill_entity(self):
        dec = DictGuardDecision(action="add_stopword", term="某词", reason="r", confidence=0.9)
        assert dec.entity_type == "skill"

    def test_position_remove_node_ok(self):
        dec = DictGuardDecision(
            entity_type="position", action="remove_node", term="SailPoint",
            reason="产品名", confidence=0.9,
        )
        assert dec.action == "remove_node"
        assert dec.entity_type == "position"

    def test_invalid_action_rejected(self):
        with pytest.raises(Exception):
            DictGuardDecision(entity_type="course", action="rename", term="x")


class TestNodeHardGate:
    def test_position_remove_vetoes_whitelist(self):
        sample = next(iter(_POSITION_WHITELIST))
        ok, reason = hard_gate("remove_node", sample, entity_type="position")
        assert ok is False
        assert "岗位白名单" in reason

    def test_position_remove_accepts_dirty(self):
        ok, reason = hard_gate("remove_node", "某英文产品名", entity_type="position")
        assert ok is True

    def test_course_remove_node_accepts(self):
        ok, _ = hard_gate("remove_node", "某孤立课程", entity_type="course")
        assert ok is True

    def test_remove_vetoes_skill_whitelist(self):
        sample = next(iter(SKILL_WHITELIST))
        assert hard_gate("remove_node", sample, entity_type="position")[0] is False

    def test_remove_edge_requires_pair_format(self):
        assert hard_gate("remove_edge", "前端→Python课程", entity_type="course")[0] is True
        ok, reason = hard_gate("remove_edge", "没有箭头的词", entity_type="course")
        assert ok is False
        assert "技能→课程" in reason


class TestNodeTierFor:
    @pytest.fixture(autouse=True)
    def _default_config(self, monkeypatch):
        monkeypatch.setattr(
            runtime_config, "get",
            lambda k, d=None: {
                "dict_guard_auto_impact_threshold": 50,
                "dict_guard_min_confidence": 0.8,
            }.get(k, d),
        )

    def test_remove_node_low_impact_high_conf_auto(self):
        assert tier_for("remove_node", True, 1, 0.9) == "auto"
        assert tier_for("remove_edge", True, 1, 0.9) == "auto"

    def test_remove_high_impact_demotes_to_proposal(self):
        assert tier_for("remove_node", True, 100, 0.9) == "proposal"

    def test_remove_low_conf_demotes_to_proposal(self):
        assert tier_for("remove_edge", True, 1, 0.5) == "proposal"

    def test_remove_gate_fail_skips(self):
        assert tier_for("remove_node", False, 0, 0.99) == "skip"


class TestSelectDirtyPositions:
    def test_zero_ref_and_empty_normalize_becomes_candidate(self):
        rows = [
            {"name": "技术", "req_count": 0, "first_seen": "2026-08-01"},  # 泛词，归一化空
            {"name": "技术", "req_count": 5, "first_seen": "2026-08-01"},  # 有引用，跳过
            {"name": next(iter(_POSITION_WHITELIST)), "req_count": 0, "first_seen": "2026-08-01"},  # 白名单
            {"name": "软件工程师", "req_count": 0, "first_seen": "2026-08-01"},  # 合法岗（可归一化）
        ]
        dirty = select_dirty_positions(rows)
        terms = [c["term"] for c in dirty]
        assert ["技术"] == terms
        assert dirty[0]["entity_type"] == "position"


class TestSelectIsolatedCourses:
    def test_isolated_courses_emitted(self):
        rows = [
            {"name": "某孤立课程", "edge_count": 0, "platform": "icourse163", "title": "标题"},
            {"name": "x", "edge_count": 0},  # 过短，跳过
            {"name": "发音打卡", "edge_count": 0, "platform": "", "title": ""},
        ]
        c = select_isolated_courses(rows)
        assert [x["term"] for x in c] == ["某孤立课程", "发音打卡"]
        assert c[0]["entity_type"] == "course"


class TestSelectDirtyCourseEdges:
    def _semantic(self):
        class _S:
            def similarity(self, a, b):
                return {"前端→垃圾课程": 0.1, "Python→Python入门": 0.9,
                        "前端→EnglishCourse": 0.2, "java→Java基础": 0.6}.get(f"{a}→{b}", 0.1)
        return _S()

    def test_low_sim_same_language_candidate(self):
        rows = [{"skill": "前端", "course": "垃圾课程", "rel_id": "1"},
                {"skill": "Python", "course": "Python入门", "rel_id": "2"},
                {"skill": "前端", "course": "EnglishCourse", "rel_id": "3"},  # 跨语言，跳过
                {"skill": "java", "course": "Java基础", "rel_id": "4"}]  # sim≥0.3，跳过
        dirty = select_dirty_course_edges(rows, self._semantic())
        assert [d["term"] for d in dirty] == ["前端→垃圾课程"]
        assert dirty[0]["entity_type"] == "course"

    def test_none_semantic_returns_empty(self):
        assert select_dirty_course_edges([{"skill": "a", "course": "b"}], None) == []
