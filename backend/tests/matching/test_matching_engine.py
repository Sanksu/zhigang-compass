"""匹配引擎单元测试（设计文档 9.4 节）。

覆盖三维评分、判零、CII 通胀修正、时效衰减、AUTO/COMPARE 模式。
评分效果示例对齐设计文档 9.4 节表格（默认权重 0.6/0.2/0.2）。
"""

from datetime import date, timedelta

import pytest

from app.services.matching.engine import (
    RuleBasedMatcher,
    apply_cii_correction,
    score_position,
    staleness_penalty,
)
from app.services.matching.schemas import (
    CandidateProfile,
    CandidateSkill,
    MatchMode,
    MatchRequest,
    Necessity,
    PositionProfile,
    SkillRequirement,
)

# 默认权重（与 configs/match_weights.json 一致）
W = (0.6, 0.2, 0.2)


def _req(skill_id: str, necessity: Necessity, weight: float = 1.0, **kw) -> SkillRequirement:
    return SkillRequirement(
        skill_id=skill_id, skill_name=skill_id, necessity=necessity, weight=weight, **kw
    )


def _candidate(skill_ids: list[str], total_years: float = 5.0) -> CandidateProfile:
    return CandidateProfile(
        user_id="u1",
        skills=[CandidateSkill(skill_id=s, skill_name=s, proficiency=2) for s in skill_ids],
        total_years=total_years,
    )


def _position(
    pid: str,
    musts: list[SkillRequirement],
    nices: list[SkillRequirement] | None = None,
    required_years: float | None = None,
    last_updated: str | None = None,
) -> PositionProfile:
    return PositionProfile(
        position_id=pid,
        name=pid,
        must_skills=musts,
        nice_skills=nices or [],
        required_years=required_years,
        last_updated=last_updated,
    )


class TestScorePosition:
    def test_perfect_match_scores_one(self):
        """完美匹配：三维满分 → total=1.0。"""
        cand = _candidate(["Python", "Java", "Go"], total_years=5)
        pos = _position(
            "p1",
            musts=[_req("Python", Necessity.MUST), _req("Java", Necessity.MUST)],
            nices=[_req("Go", Necessity.NICE)],
            required_years=3,
        )
        result = score_position(cand, pos, weights=W)
        assert result.total_score == 1.0
        assert result.unqualified is False

    def test_missing_one_of_twenty_must_scores_097(self):
        """缺 1/20 must → must_score=0.95，total=0.97（设计文档 9.4 示例）。

        注：文档示例假定无 CII 修正；实际 20 个必备技能会触发 CII 降级（见
        test_cii_affects_scoring），此处用不触发 CII 的构造验证 matched/total 公式。
        """
        cand = _candidate([f"sk{i}" for i in range(1, 20)])  # 缺 sk20
        musts = [_req(f"sk{i}", Necessity.MUST) for i in range(1, 21)]
        pos = _position("p1", musts=musts)
        result = score_position(cand, pos, weights=W)
        # CII：20 must > 7 → 最低权重 20%（4 个）降级为 nice，must 剩 16 个
        assert result.must_score == pytest.approx(15 / 16)
        assert result.nice_score == 1.0  # 降级的 4 个技能候选人均已具备
        assert result.total_score == pytest.approx(15 / 16 * 0.6 + 0.2 + 0.2)
        assert result.missing_must == ["sk20"]

    def test_missing_skill_uses_matched_over_total(self):
        """无 CII 场景：缺 1/5 must → must_score=0.8。"""
        cand = _candidate(["sk1", "sk2", "sk3", "sk4"])
        pos = _position(
            "p1",
            musts=[_req(f"sk{i}", Necessity.MUST) for i in range(1, 6)],
        )
        result = score_position(cand, pos, weights=W)
        assert result.must_score == pytest.approx(0.8)
        assert result.missing_must == ["sk5"]

    def test_all_must_missing_returns_zero(self):
        """必备技能全缺失 → total=0.0 且 unqualified。"""
        cand = _candidate(["Python"])
        pos = _position("p1", musts=[_req("Java", Necessity.MUST)])
        result = score_position(cand, pos, weights=W)
        assert result.total_score == 0.0
        assert result.unqualified is True
        assert "未达门槛" in result.summary

    def test_low_experience_scales_exp_score(self):
        """经验不足：required 4 年 candidate 2 年 → exp=0.5 → total=0.9。"""
        cand = _candidate(["Python", "Java", "Go"], total_years=2)
        pos = _position(
            "p1",
            musts=[_req("Python", Necessity.MUST), _req("Java", Necessity.MUST)],
            nices=[_req("Go", Necessity.NICE)],
            required_years=4,
        )
        result = score_position(cand, pos, weights=W)
        assert result.exp_score == pytest.approx(0.5)
        assert result.total_score == pytest.approx(0.9)

    def test_no_required_years_means_full_exp(self):
        """无年限要求 → exp_score=1.0。"""
        cand = _candidate(["Python", "Java"], total_years=0)
        pos = _position("p1", musts=[_req("Python", Necessity.MUST)], required_years=None)
        result = score_position(cand, pos, weights=W)
        assert result.exp_score == 1.0

    def test_no_nice_skills_does_not_penalize(self):
        """岗位无加分技能 → nice_score=1.0 不扣分。"""
        cand = _candidate(["Python"])
        pos = _position("p1", musts=[_req("Python", Necessity.MUST)])
        result = score_position(cand, pos, weights=W)
        assert result.nice_score == 1.0

    def test_no_must_skills_gives_full_must(self):
        """岗位无必备技能 → must_score=1.0，不判零。"""
        cand = _candidate([])
        pos = _position("p1", musts=[])
        result = score_position(cand, pos, weights=W)
        assert result.must_score == 1.0
        assert result.unqualified is False

    def test_nice_score_is_weighted_average(self):
        """nice_score = 命中技能权重和 / 总权重。"""
        cand = _candidate(["Go"])
        pos = _position(
            "p1",
            musts=[],
            nices=[
                _req("Go", Necessity.NICE, weight=3.0),
                _req("Rust", Necessity.NICE, weight=1.0),
            ],
        )
        result = score_position(cand, pos, weights=W)
        assert result.nice_score == pytest.approx(0.75)

    def test_staleness_mild_and_strong(self):
        """时效衰减：>180d → 0.95，>365d → 0.85，新鲜 → 1.0。"""
        today = date(2026, 8, 1)
        assert staleness_penalty((today - timedelta(days=200)).isoformat(), today) == 0.95
        assert staleness_penalty((today - timedelta(days=400)).isoformat(), today) == 0.85
        assert staleness_penalty((today - timedelta(days=30)).isoformat(), today) == 1.0

    def test_staleness_missing_or_invalid_is_fresh(self):
        assert staleness_penalty(None) == 1.0
        assert staleness_penalty("not-a-date") == 1.0

    def test_staleness_applies_to_total(self):
        """过期岗位 total 按衰减系数折算。"""
        today = date(2026, 8, 1)
        stale = (today - timedelta(days=200)).isoformat()
        cand = _candidate(["Python", "Java", "Go"])
        pos = _position(
            "p1",
            musts=[_req("Python", Necessity.MUST), _req("Java", Necessity.MUST)],
            nices=[_req("Go", Necessity.NICE)],
            required_years=3,
            last_updated=stale,
        )
        result = score_position(cand, pos, weights=W, today=today)
        assert result.total_score == pytest.approx(0.95)


class TestCII:
    def test_demotes_lowest_weight_20_percent(self):
        """10 个必备技能，最低权重 20%（2 个）降级为 nice。"""
        musts = [_req(f"sk{i}", Necessity.MUST, weight=1.0) for i in range(1, 9)]
        musts += [_req("sk9", Necessity.MUST, weight=0.1), _req("sk10", Necessity.MUST, weight=0.1)]
        pos = _position("p1", musts=musts)
        corrected = apply_cii_correction(pos)
        assert len(corrected.must_skills) == 8
        assert len(corrected.nice_skills) == 2
        assert {s.skill_id for s in corrected.nice_skills} == {"sk9", "sk10"}
        assert corrected.nice_skills[0].necessity == Necessity.NICE

    def test_protects_core_skill_from_demotion(self):
        """专家熟练度且跨 ≥30 源的核心技能不降级。"""
        musts = [_req(f"sk{i}", Necessity.MUST, weight=0.1) for i in range(1, 9)]
        musts.append(
            _req("core", Necessity.MUST, weight=0.1, proficiency="专家", source_count=40)
        )
        pos = _position("p1", musts=musts)
        corrected = apply_cii_correction(pos)
        assert "core" in {s.skill_id for s in corrected.must_skills}

    def test_no_correction_within_threshold(self):
        pos = _position("p1", musts=[_req("sk1", Necessity.MUST) for _ in range(7)])
        corrected = apply_cii_correction(pos)
        assert len(corrected.must_skills) == 7
        assert len(corrected.nice_skills) == 0

    def test_cii_affects_scoring(self):
        """CII 降级后 must 命中率按降级后的集合计算。"""
        musts = [_req(f"sk{i}", Necessity.MUST, weight=1.0) for i in range(1, 9)]
        musts += [_req("edge1", Necessity.MUST, weight=0.1), _req("edge2", Necessity.MUST, weight=0.1)]
        cand = _candidate([f"sk{i}" for i in range(1, 9)])  # 命中 8 个保留 must
        pos = _position("p1", musts=musts)
        result = score_position(cand, pos, weights=W)
        assert result.must_score == 1.0  # 8/8，降级的 edge 不计入


class TestSynonymMatch:
    """技能别名级同义词匹配（设计文档 9.4：别名级 1.0）。"""

    def test_golang_matches_go(self):
        """候选人写 "Golang"，岗位要求 "Go" → 规范名归一后命中。"""
        cand = _candidate(["Golang", "Python"], total_years=5)
        pos = _position(
            "p1",
            musts=[_req("Go", Necessity.MUST), _req("Python", Necessity.MUST)],
        )
        result = score_position(cand, pos, weights=W)
        assert result.must_score == 1.0
        assert result.missing_must == []

    def test_spring_matches_spring_boot(self):
        """候选人写 "Spring"，岗位要求 "Spring Boot" → 命中。"""
        cand = _candidate(["Spring"])
        pos = _position("p1", musts=[_req("Spring Boot", Necessity.MUST)])
        result = score_position(cand, pos, weights=W)
        assert result.must_score == 1.0

    def test_skill_id_exact_still_works(self):
        """skill_id 精确匹配优先于名称比较。"""
        cand = CandidateProfile(
            user_id="u1",
            skills=[CandidateSkill(skill_id="sk_001", skill_name="无关名", proficiency=2)],
        )
        pos = _position("p1", musts=[_req("sk_001", Necessity.MUST)])
        result = score_position(cand, pos, weights=W)
        assert result.must_score == 1.0

    def test_rough_select_counts_synonym_hits(self):
        """粗筛 hit_count 以规范名交集计数，别名变体可入围。"""
        cand = _candidate(["golang"], total_years=3)
        positions = [
            _position("go_pos", musts=[_req("Go", Necessity.MUST)]),
            _position("rust_pos", musts=[_req("Rust", Necessity.MUST)]),
        ]
        matcher = RuleBasedMatcher(positions)
        results = matcher.match(MatchRequest(candidate=cand, mode=MatchMode.AUTO, top_n=2))
        assert results[0].position_id == "go_pos"


class _FakeSemantic:
    """固定相似度表的假语义器（测试注入）。"""

    def __init__(self, table: dict[tuple[str, str], float], fail: bool = False):
        self.table = table
        self.fail = fail

    def similarity(self, a: str, b: str) -> float:
        if self.fail:
            raise RuntimeError("模型不可用")
        return self.table.get((a, b), 0.0)


class TestSemanticMatching:
    """语义级同义词匹配（设计文档 9.4：语义级 0.85-1.0，sim ≥ threshold 计入）。"""

    def test_semantic_hit_gets_partial_credit(self):
        """语义命中按相似度值计入 must_score（非布尔）。"""
        cand = _candidate(["机器学习"])
        pos = _position("p1", musts=[_req("深度学习", Necessity.MUST)])
        sem = _FakeSemantic({("深度学习", "机器学习"): 0.64})
        result = score_position(cand, pos, weights=W, semantic=sem, sim_threshold=0.5)
        assert result.must_score == pytest.approx(0.64)
        assert "深度学习" in result.matched_must

    def test_semantic_below_threshold_misses(self):
        """相似度低于阈值不计入（语义不武断命中）。"""
        cand = _candidate(["机器学习"])
        pos = _position("p1", musts=[_req("深度学习", Necessity.MUST)])
        sem = _FakeSemantic({("深度学习", "机器学习"): 0.4})
        result = score_position(cand, pos, weights=W, semantic=sem, sim_threshold=0.5)
        assert result.must_score == 0.0
        assert result.unqualified is True

    def test_semantic_unavailable_degrades_to_rule(self):
        """语义模型异常时降级纯规则（不阻断、不误命中）。"""
        cand = _candidate(["Python"])
        pos = _position("p1", musts=[_req("Go", Necessity.MUST)])
        sem = _FakeSemantic({}, fail=True)
        result = score_position(cand, pos, weights=W, semantic=sem)
        assert result.must_score == 0.0
        assert result.missing_must == ["Go"]

    def test_alias_exact_wins_over_semantic(self):
        """别名级精确匹配返回 1.0，不因语义分数拉低。"""
        cand = _candidate(["Go"])
        pos = _position("p1", musts=[_req("Go", Necessity.MUST)])
        sem = _FakeSemantic({("Go", "Go"): 0.9})  # 即使语义只有 0.9
        result = score_position(cand, pos, weights=W, semantic=sem, sim_threshold=0.85)
        assert result.must_score == 1.0


class TestRuleBasedMatcher:
    def test_auto_returns_sorted_top_n(self):
        """AUTO 模式按 total DESC 截取 Top-N。"""
        cand = _candidate(["Python", "Java", "Go"], total_years=5)
        positions = [
            _position("low", musts=[_req("Rust", Necessity.MUST)]),
            _position(
                "high",
                musts=[_req("Python", Necessity.MUST), _req("Java", Necessity.MUST)],
                nices=[_req("Go", Necessity.NICE)],
            ),
            _position("mid", musts=[_req("Python", Necessity.MUST)]),
        ]
        matcher = RuleBasedMatcher(positions)
        results = matcher.match(MatchRequest(candidate=cand, mode=MatchMode.AUTO, top_n=2))
        assert [r.position_id for r in results] == ["high", "mid"]
        assert results[0].total_score >= results[1].total_score

    def test_auto_does_not_include_unqualified_first(self):
        """判零岗位排在末尾。"""
        cand = _candidate(["Python"])
        positions = [
            _position("zero", musts=[_req("Java", Necessity.MUST)]),
            _position("ok", musts=[_req("Python", Necessity.MUST)]),
        ]
        matcher = RuleBasedMatcher(positions)
        results = matcher.match(MatchRequest(candidate=cand, mode=MatchMode.AUTO, top_n=10))
        assert results[0].position_id == "ok"
        assert results[-1].position_id == "zero"
        assert results[-1].unqualified is True

    def test_compare_returns_single_position(self):
        cand = _candidate(["Python"])
        matcher = RuleBasedMatcher([_position("p1", musts=[_req("Python", Necessity.MUST)])])
        results = matcher.match(
            MatchRequest(candidate=cand, mode=MatchMode.COMPARE, target_position_id="p1")
        )
        assert len(results) == 1
        assert results[0].position_id == "p1"

    def test_compare_missing_target_raises(self):
        matcher = RuleBasedMatcher([])
        with pytest.raises(ValueError):
            matcher.match(MatchRequest(candidate=_candidate([]), mode=MatchMode.COMPARE))

    def test_compare_unknown_position_raises(self):
        matcher = RuleBasedMatcher([_position("p1", musts=[])])
        with pytest.raises(ValueError, match="目标岗位不存在"):
            matcher.match(
                MatchRequest(candidate=_candidate([]), mode=MatchMode.COMPARE, target_position_id="nope")
            )
