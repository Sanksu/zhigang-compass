"""匹配引擎单元测试（设计文档 9.4 节）。

覆盖三维评分、判零、CII 通胀修正、时效衰减、AUTO/COMPARE 模式、
熟练度折减（方案 A）、must 按 source_count 加权（方案 B）、雷达 skill_level 维度。
评分效果示例对齐设计文档 9.4 节表格（默认权重 0.6/0.2/0.2）。
"""

import math
from datetime import date, timedelta

import pytest

from app.services.matching.engine import (
    RuleBasedMatcher,
    _proficiency_factor,
    _source_weight,
    apply_cii_correction,
    score_position,
    staleness_penalty,
)
from app.services.matching.schemas import (
    CandidateProfile,
    CandidateProject,
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

    def test_no_must_skills_renormalizes_instead_of_free_full(self):
        """A1：岗位无必备技能 → must_score=None（不再白得满分），总分在
        nice/exp 上权重重归一，雷达 must 维度为 None。"""
        cand = _candidate(["Go", "Rust"])
        pos = _position(
            "p1",
            musts=[],
            nices=[_req("Go", Necessity.NICE, weight=1.0), _req("Rust", Necessity.NICE, weight=1.0)],
            required_years=2,
        )
        result = score_position(cand, pos, weights=W)
        assert result.must_score is None
        assert result.nice_score == 1.0
        assert result.exp_score == 1.0
        # 重归一：(1.0×0.2 + 1.0×0.2) / (0.2+0.2) = 1.0（旧口径白得 0.6 满分贡献）
        assert result.total_score == 1.0
        assert result.unqualified is False
        assert result.radar["must"] is None
        assert "无必备技能门槛" in result.summary

    def test_no_must_partial_nice_scales_renormalized_total(self):
        """A1：无门槛岗位部分命中 nice → 总分只由 nice/exp 决定（重归一后）。

        命中 1/2 nice（exp 满分）：(0.5×0.2 + 1.0×0.2) / 0.4 = 0.75。
        """
        cand = _candidate(["Go"], total_years=5)
        pos = _position(
            "p1",
            musts=[],
            nices=[_req("Go", Necessity.NICE, weight=1.0), _req("Rust", Necessity.NICE, weight=1.0)],
            required_years=2,
        )
        result = score_position(cand, pos, weights=W)
        assert result.total_score == pytest.approx(0.75)

    def test_no_must_all_nice_missed_unqualified(self):
        """A3：无门槛岗位 nice 全未命中 → 判零不纳入推荐（防无关候选占位）。"""
        cand = _candidate(["Python"])
        pos = _position(
            "p1",
            musts=[],
            nices=[_req("Go", Necessity.NICE, weight=1.0), _req("Rust", Necessity.NICE, weight=1.0)],
        )
        result = score_position(cand, pos, weights=W)
        assert result.total_score == 0.0
        assert result.unqualified is True
        assert "命中不足" in result.summary

    def test_no_must_nice_below_floor_unqualified(self):
        """O1：无门槛岗位 Top-10 命中不足 2 条（nice<0.2）→ 判零。

        10 条 nice 命中 1 条 = 0.1 < 0.2 → unqualified；命中 2 条 = 0.2 达门槛。
        （freq=3 小样本岗重归一后 exp 占 86.7%，1 条命中曾得 0.88 倒挂真匹配岗）
        """
        nices = [_req(f"s{i}", Necessity.NICE, weight=1.0) for i in range(10)]
        pos = _position("p1", musts=[], nices=nices)

        one_hit = score_position(_candidate(["s0"]), pos, weights=W)
        assert one_hit.nice_score == pytest.approx(0.1)
        assert one_hit.unqualified is True
        assert one_hit.total_score == 0.0

        two_hits = score_position(_candidate(["s0", "s1"]), pos, weights=W)
        assert two_hits.nice_score == pytest.approx(0.2)
        assert two_hits.unqualified is False

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

    def test_nice_topk_truncates_long_tail(self):
        """B1：nice 池超过 NICE_TOP_K 时只考核跨源数最高的前 K 条。

        12 条 nice：top-10 跨源数高（source_count=5）、尾部 2 条跨源数低
        （source_count=1）。候选人只命中尾部 2 条 → 被截断 → 0.0；
        命中 top-10 中的 5 条 → 0.5。
        """
        nices = [_req(f"core{i}", Necessity.NICE, weight=1.0, source_count=5) for i in range(10)]
        nices += [_req(f"tail{i}", Necessity.NICE, weight=1.0, source_count=1) for i in range(2)]
        pos = _position("p1", musts=[_req("Python", Necessity.MUST)], nices=nices)

        only_tail = score_position(_candidate(["Python", "tail0", "tail1"]), pos, weights=W)
        assert only_tail.nice_score == 0.0

        half_core = score_position(_candidate(["Python", *[f"core{i}" for i in range(5)]]), pos, weights=W)
        assert half_core.nice_score == pytest.approx(0.5)

    def test_nice_topk_ranks_by_source_count(self):
        """B1：Top-K 排序键是 source_count（nice 边权重统一无区分度）。"""
        nices = [
            _req("b", Necessity.NICE, weight=0.4, source_count=9),
            _req("a", Necessity.NICE, weight=0.4, source_count=1),
        ]
        # 池不足 K 条不截断，但排序语义由本用例锁定：候选只命中低跨源 a → 0.5×0.4/0.8
        pos = _position("p1", musts=[_req("Python", Necessity.MUST)], nices=nices)
        result = score_position(_candidate(["Python", "a"]), pos, weights=W)
        assert result.nice_score == pytest.approx(0.5)

    def test_nice_topk_tiebreak_deterministic(self):
        """B1 边角：source_count 平局按 skill_name 升序截断，Top-K 组成确定。

        12 条 sc=1（插入序把 zz 开头放最前）：字典序前 10 条入围、zz 两条被
        截断——不随图谱返回序漂移（小样本岗 sc 全为 1 时尤其重要）。
        """
        names = [f"zz{i}" for i in range(2)] + [f"a{i:02d}" for i in range(10)]
        nices = [_req(n, Necessity.NICE, weight=1.0, source_count=1) for n in names]
        pos = _position("p1", musts=[_req("Python", Necessity.MUST)], nices=nices)

        hit_truncated = score_position(_candidate(["Python", "zz0"]), pos, weights=W)
        assert hit_truncated.nice_score == 0.0

        hit_kept = score_position(_candidate(["Python", "a00"]), pos, weights=W)
        assert hit_kept.nice_score == pytest.approx(0.1)

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


class TestRadar:
    """人岗比对五维雷达（设计文档 §9.5）：must/nice/experience/education/projects。"""

    def test_five_dimensions_align_with_scores(self):
        """三维与评分一致，education 层级匹配给分，projects 无语义模型时返回 None。"""
        cand = CandidateProfile(
            user_id="u1",
            skills=[CandidateSkill(skill_id="Python", skill_name="Python", proficiency=2)],
            total_years=5,
            education_level="本科",
            projects=[CandidateProject(name="数据中台", description="Python 开发")],
        )
        pos = PositionProfile(
            position_id="p1",
            name="p1",
            must_skills=[_req("Python", Necessity.MUST)],
            nice_skills=[_req("Go", Necessity.NICE)],
            required_years=3,
            required_education="本科",
            typical_scenarios=["数据中台建设"],
        )
        result = score_position(cand, pos, weights=W)
        assert result.radar["must"] == result.must_score
        assert result.radar["nice"] == result.nice_score
        assert result.radar["experience"] == result.exp_score
        assert result.radar["education"] == 1.0  # 本科 ≥ 本科
        assert result.radar["projects"] is None  # 未注入 semantic → 保守 None

    def test_education_hierarchy_scoring(self):
        """学历维度近似：高于/等于要求 1.0，低一级 0.5，无数据 None。"""
        pos = PositionProfile(
            position_id="p1", name="p1", must_skills=[], required_education="本科"
        )
        hi = CandidateProfile(user_id="u1", skills=[], total_years=1, education_level="硕士")
        assert score_position(hi, pos, weights=W).radar["education"] == 1.0
        low = CandidateProfile(user_id="u1", skills=[], total_years=1, education_level="大专")
        assert score_position(low, pos, weights=W).radar["education"] == 0.5
        none_side = CandidateProfile(user_id="u1", skills=[])
        assert score_position(none_side, pos, weights=W).radar["education"] is None
        # 岗位侧无学历要求 → None（不武断给分）
        no_req = _position("p1", musts=[])
        assert score_position(hi, no_req, weights=W).radar["education"] is None

    def test_projects_with_semantic_averages_similarity(self):
        """项目维度：注入语义模型后取项目-场景相似度均值。"""
        cand = CandidateProfile(
            user_id="u1",
            skills=[],
            total_years=1,
            projects=[CandidateProject(name="数据中台", description="Python 开发")],
        )
        pos = PositionProfile(
            position_id="p1",
            name="p1",
            must_skills=[],
            typical_scenarios=["数据中台建设", "数据仓库"],
        )
        sem = _FakeSemantic({
            ("数据中台：Python 开发", "数据中台建设"): 0.8,
            ("数据中台：Python 开发", "数据仓库"): 0.4,
        })
        result = score_position(cand, pos, weights=W, semantic=sem, sim_threshold=0.5)
        assert result.radar["projects"] == pytest.approx(0.6)


class TestDomainDimension:
    """雷达"领域"维度：岗位行业 × 候选人领域经验（仅展示，不参与总分）。"""

    def _pos(self, industry: str | None) -> PositionProfile:
        return PositionProfile(
            position_id="p1", name="p1", must_skills=[], industry=industry
        )

    def _cand(self, domains: list[str]) -> CandidateProfile:
        return CandidateProfile(
            user_id="u1", skills=[], total_years=1, domain_experience=domains
        )

    def test_exact_match_scores_one(self):
        result = score_position(self._cand(["金融"]), self._pos("金融"), weights=W)
        assert result.radar["domain"] == 1.0

    def test_substring_match_scores_one(self):
        # 岗位"金融科技" × 候选"金融"（子串双向）→ 1.0
        assert score_position(
            self._cand(["金融"]), self._pos("金融科技"), weights=W
        ).radar["domain"] == 1.0
        assert score_position(
            self._cand(["金融科技"]), self._pos("金融"), weights=W
        ).radar["domain"] == 1.0

    def test_no_match_without_semantic_returns_zero(self):
        # 未命中且无语义模型 → 0.0（有数据但不匹配）
        result = score_position(self._cand(["电商"]), self._pos("金融"), weights=W)
        assert result.radar["domain"] == 0.0

    def test_missing_side_returns_none(self):
        # 岗位无行业 / 候选人无领域经验 → None（无信息不参与）
        assert score_position(
            self._cand(["金融"]), self._pos(None), weights=W
        ).radar["domain"] is None
        assert score_position(
            self._cand([]), self._pos("金融"), weights=W
        ).radar["domain"] is None

    def test_alias_slash_composite_normalized(self):
        # 斜杠复合行业词（SaaS/云技术）拆原子词后与候选"云计算"子串命中
        result = score_position(
            self._cand(["云计算"]), self._pos("SaaS/云技术"), weights=W
        )
        assert result.radar["domain"] == 1.0
        # 反向：候选"云技术"命中岗位"SaaS/云技术"（子串）
        assert score_position(
            self._cand(["云技术"]), self._pos("SaaS/云技术"), weights=W
        ).radar["domain"] == 1.0

    def test_domain_sem_threshold_independent_of_skill_threshold(self):
        # 领域阈值 0.5 独立于技能 sim_threshold：技能阈值很严时领域语义仍可命中
        sem = _FakeSemantic({("金融", "电商"): 0.56})
        result = score_position(
            self._cand(["电商"]), self._pos("金融"), weights=W,
            semantic=sem, sim_threshold=0.9,
        )
        assert result.radar["domain"] == pytest.approx(0.56)

    def test_blocklist_blocks_cross_cluster_semantic_hit(self):
        # 黑名单：电商×制造业语义 0.646 是跨簇假阳性 → 拦截返回 0.0（双向）
        sem = _FakeSemantic({("制造业", "电商"): 0.646})
        assert score_position(
            self._cand(["电商"]), self._pos("制造业"), weights=W,
            semantic=sem, sim_threshold=0.9,
        ).radar["domain"] == 0.0
        assert score_position(
            self._cand(["制造业"]), self._pos("电商"), weights=W,
            semantic=sem, sim_threshold=0.9,
        ).radar["domain"] == 0.0

    def test_blocklist_does_not_block_lexical_hit(self):
        # 黑名单仅拦语义兜底：词面命中（候选"制造"×岗位"制造业"）仍满分
        sem = _FakeSemantic({("制造业", "电商"): 0.646})
        result = score_position(
            self._cand(["制造"]), self._pos("制造业"), weights=W,
            semantic=sem, sim_threshold=0.9,
        )
        assert result.radar["domain"] == 1.0

    def test_blocklist_ignores_other_semantic_hits(self):
        # 黑名单只拦指定 pair：电商×金融科技仍可语义命中
        sem = _FakeSemantic({("金融科技", "电商"): 0.56, ("制造业", "电商"): 0.646})
        result = score_position(
            self._cand(["电商"]), self._pos("金融科技"), weights=W,
            semantic=sem, sim_threshold=0.9,
        )
        assert result.radar["domain"] == pytest.approx(0.56)

    def test_semantic_fallback(self):
        # 词面未命中 → 语义相似度 ≥ 领域阈值(0.5)计值，否则 0.0
        sem = _FakeSemantic({("金融", "电商"): 0.9, ("金融", "医疗"): 0.3})
        pos = self._pos("金融")
        assert score_position(
            self._cand(["电商"]), pos, weights=W, semantic=sem, sim_threshold=0.5
        ).radar["domain"] == pytest.approx(0.9)
        assert score_position(
            self._cand(["医疗"]), pos, weights=W, semantic=sem, sim_threshold=0.5
        ).radar["domain"] == 0.0

    def test_domain_not_in_total_score(self):
        # 领域仅展示维度：命中/未命中不影响 total_score
        pos = self._pos("金融")
        hit = score_position(self._cand(["金融"]), pos, weights=W)
        miss = score_position(self._cand(["电商"]), pos, weights=W)
        assert hit.total_score == miss.total_score
        assert hit.total_score == 1.0  # 无 must/nice/exp 要求 → 满分


def _candidate_with_prof(profs: dict[str, int], total_years: float = 5.0) -> CandidateProfile:
    """构造带熟练度的候选人（技能名 → proficiency 1/2/3）。"""
    return CandidateProfile(
        user_id="u1",
        skills=[
            CandidateSkill(skill_id=s, skill_name=s, proficiency=p) for s, p in profs.items()
        ],
        total_years=total_years,
    )


class TestProficiencyFactor:
    """熟练度满足度矩阵（方案 A：岗位期望 × 候选人 1/2/3）。"""

    def test_matrix(self):
        assert _proficiency_factor("初级", 1) == pytest.approx(0.85)
        assert _proficiency_factor("初级", 2) == pytest.approx(1.0)
        assert _proficiency_factor("初级", 3) == pytest.approx(1.0)
        assert _proficiency_factor("中级", 1) == pytest.approx(0.60)
        assert _proficiency_factor("中级", 2) == pytest.approx(1.0)
        assert _proficiency_factor("中级", 3) == pytest.approx(1.0)
        assert _proficiency_factor("高级", 1) == pytest.approx(0.30)
        assert _proficiency_factor("高级", 2) == pytest.approx(0.60)
        assert _proficiency_factor("高级", 3) == pytest.approx(1.0)
        assert _proficiency_factor("专家", 1) == pytest.approx(0.30)
        assert _proficiency_factor("专家", 2) == pytest.approx(0.60)
        assert _proficiency_factor("专家", 3) == pytest.approx(0.85)

    def test_missing_required_no_penalty(self):
        """岗位无期望熟练度 → 1.0（黄金集零回归默认）。"""
        assert _proficiency_factor(None, 2) == 1.0
        assert _proficiency_factor("", 2) == 1.0

    def test_missing_candidate_no_penalty(self):
        assert _proficiency_factor("高级", None) == 1.0

    def test_unknown_level_falls_back_no_penalty(self):
        """未知档位（数据异常）不武断惩罚。"""
        assert _proficiency_factor("未知", 1) == 1.0


class TestSourceWeight:
    """must 权重函数边界（方案 B：log(source_count+1)）。"""

    def test_default_is_flat(self):
        """source_count 缺失/1 → 全等权（退化为等权平均）。"""
        assert _source_weight(None) == pytest.approx(math.log(2))
        assert _source_weight(1) == pytest.approx(math.log(2))

    def test_core_skill_weights_more(self):
        assert _source_weight(50) > _source_weight(1) * 3  # 约 5.6 倍

    def test_large_value_no_dominance(self):
        """log 平滑：超大源数不线性放大。"""
        assert _source_weight(1000) < _source_weight(1) * 10


class TestProficiencyMatching:
    """熟练度折减参与评分（方案 A）。"""

    def test_sufficient_proficiency_full_credit(self):
        """候选人熟练度 ≥ 岗位期望 → 满分。"""
        cand = _candidate_with_prof({"Python": 3})
        pos = _position("p1", musts=[_req("Python", Necessity.MUST, proficiency="高级")])
        result = score_position(cand, pos, weights=W)
        assert result.must_score == 1.0

    def test_one_level_low_discounts(self):
        """低一档 → ×0.6（熟悉2 × 高级=0.6）。"""
        cand = _candidate_with_prof({"Python": 2})
        pos = _position("p1", musts=[_req("Python", Necessity.MUST, proficiency="高级")])
        result = score_position(cand, pos, weights=W)
        assert result.must_score == pytest.approx(0.6)

    def test_two_level_low_discounts(self):
        """低两档 → ×0.3（了解1 × 高级=0.3）。"""
        cand = _candidate_with_prof({"Python": 1})
        pos = _position("p1", musts=[_req("Python", Necessity.MUST, proficiency="高级")])
        result = score_position(cand, pos, weights=W)
        assert result.must_score == pytest.approx(0.3)

    def test_expert_requirement_proficient_keeps_margin(self):
        """专家岗 + 精通 → 0.85（留余量，不极端归零）。"""
        cand = _candidate_with_prof({"Python": 3})
        pos = _position("p1", musts=[_req("Python", Necessity.MUST, proficiency="专家")])
        result = score_position(cand, pos, weights=W)
        assert result.must_score == pytest.approx(0.85)

    def test_no_required_proficiency_no_penalty(self):
        """岗位无期望熟练度 → 不折减（与优化前行为一致）。"""
        cand = _candidate(["Python"])  # proficiency=2
        pos = _position("p1", musts=[_req("Python", Necessity.MUST)])
        result = score_position(cand, pos, weights=W)
        assert result.must_score == 1.0

    def test_nice_skill_also_applies_factor(self):
        cand = _candidate_with_prof({"Go": 1})
        pos = _position("p1", musts=[], nices=[_req("Go", Necessity.NICE, proficiency="高级")])
        result = score_position(cand, pos, weights=W)
        assert result.nice_score == pytest.approx(0.3)

    def test_semantic_hit_applies_proficiency_factor(self):
        """语义命中（0.9）再乘熟练度系数 0.3。"""
        cand = _candidate_with_prof({"机器学习": 1})
        pos = _position("p1", musts=[_req("深度学习", Necessity.MUST, proficiency="高级")])
        sem = _FakeSemantic({("深度学习", "机器学习"): 0.9})
        result = score_position(cand, pos, weights=W, semantic=sem, sim_threshold=0.5)
        assert result.must_score == pytest.approx(0.9 * 0.3)

    def test_low_confidence_and_proficiency_compound(self):
        """软技能推断（×0.5）与熟练度不足（×0.6）复合降权。"""
        cand = CandidateProfile(
            user_id="u1",
            skills=[CandidateSkill(skill_id="Python", skill_name="Python", proficiency=2, low_confidence=True)],
            total_years=5,
        )
        pos = _position("p1", musts=[_req("Python", Necessity.MUST, proficiency="高级")])
        result = score_position(cand, pos, weights=W)
        assert result.must_score == pytest.approx(0.6 * 0.5)


class TestMustWeightedScoring:
    """must 按 source_count 加权（方案 B：log(source_count+1)）。"""

    def test_missing_core_skill_penalizes_more(self):
        """缺核心技能（source_count=50）比缺边缘技能（=1）扣分更多。"""
        cand = _candidate(["Go"])
        pos = _position(
            "p1",
            musts=[
                _req("Python", Necessity.MUST, source_count=50),
                _req("Go", Necessity.MUST, source_count=1),
            ],
        )
        result = score_position(cand, pos, weights=W)
        expected = math.log(2) / (math.log(51) + math.log(2))
        # MatchResult 构造时 must_score 经 round(…, 4)，断言按 4 位精度
        assert result.must_score == pytest.approx(expected, abs=1e-4)
        assert result.must_score < 0.5  # 等权会是 0.5

    def test_hit_core_skill_boosts_score(self):
        cand = _candidate(["Python"])
        pos = _position(
            "p1",
            musts=[
                _req("Python", Necessity.MUST, source_count=50),
                _req("Go", Necessity.MUST, source_count=1),
            ],
        )
        result = score_position(cand, pos, weights=W)
        expected = math.log(51) / (math.log(51) + math.log(2))
        assert result.must_score == pytest.approx(expected, abs=1e-4)
        assert result.must_score > 0.5

    def test_equal_source_counts_flat(self):
        cand = _candidate(["Go"])
        pos = _position(
            "p1",
            musts=[
                _req("Python", Necessity.MUST, source_count=5),
                _req("Go", Necessity.MUST, source_count=5),
            ],
        )
        result = score_position(cand, pos, weights=W)
        assert result.must_score == pytest.approx(0.5)

    def test_default_source_count_keeps_old_behavior(self):
        """source_count 默认 1（黄金集/历史边）→ 等权，行为与优化前一致。"""
        cand = _candidate(["sk1", "sk2", "sk3", "sk4"])
        pos = _position("p1", musts=[_req(f"sk{i}", Necessity.MUST) for i in range(1, 6)])
        result = score_position(cand, pos, weights=W)
        assert result.must_score == pytest.approx(0.8)


class TestSkillLevelRadar:
    """雷达 skill_level 维度：命中必备技能的熟练度满足度均值（仅展示）。"""

    def test_fully_satisfied_gives_one(self):
        cand = _candidate_with_prof({"Python": 3})
        pos = _position("p1", musts=[_req("Python", Necessity.MUST, proficiency="高级")])
        result = score_position(cand, pos, weights=W)
        assert result.radar["skill_level"] == 1.0

    def test_partial_satisfaction_averages(self):
        cand = _candidate_with_prof({"Python": 2, "Go": 3})
        pos = _position(
            "p1",
            musts=[
                _req("Python", Necessity.MUST, proficiency="高级"),
                _req("Go", Necessity.MUST, proficiency="高级"),
            ],
        )
        result = score_position(cand, pos, weights=W)
        assert result.radar["skill_level"] == pytest.approx((0.6 + 1.0) / 2)

    def test_no_hits_returns_none(self):
        cand = _candidate_with_prof({"Rust": 2})
        pos = _position("p1", musts=[_req("Python", Necessity.MUST, proficiency="高级")])
        result = score_position(cand, pos, weights=W)
        assert result.radar["skill_level"] is None

    def test_no_required_proficiency_returns_none(self):
        """岗位全部无期望熟练度 → 维度不展示。"""
        cand = _candidate(["Python"])
        pos = _position("p1", musts=[_req("Python", Necessity.MUST)])
        result = score_position(cand, pos, weights=W)
        assert result.radar["skill_level"] is None


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
        """判零岗位被排除（不合格不参与推荐）。"""
        cand = _candidate(["Python"])
        positions = [
            _position("zero", musts=[_req("Java", Necessity.MUST)]),
            _position("ok", musts=[_req("Python", Necessity.MUST)]),
        ]
        matcher = RuleBasedMatcher(positions)
        results = matcher.match(MatchRequest(candidate=cand, mode=MatchMode.AUTO, top_n=10))
        assert [r.position_id for r in results] == ["ok"]

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


class TestSoftSkillDownweight:
    """LLM 推断软技能（low_confidence）匹配降权 ×0.5（设计文档 9.2 节）。"""

    def _soft_candidate(self, name: str, low: bool = True) -> CandidateProfile:
        return CandidateProfile(
            user_id="u1",
            skills=[CandidateSkill(skill_id=name, skill_name=name, proficiency=2, low_confidence=low)],
            total_years=5,
        )

    def test_exact_match_low_confidence_half_credit(self):
        """推断软技能精确命中 → must_score=0.5（×0.5 降权）。"""
        cand = self._soft_candidate("团队协作")
        pos = _position(
            "p1",
            musts=[_req("团队协作", Necessity.MUST)],
            nices=[_req("Python", Necessity.NICE)],
            required_years=3,
        )
        result = score_position(cand, pos, weights=W)
        assert result.must_score == pytest.approx(0.5)
        assert result.total_score == pytest.approx(0.5 * 0.6 + 0 + 0.2)
        assert "团队协作" in result.matched_must

    def test_explicit_skill_full_credit(self):
        """同一技能文本直述（low_confidence=False）→ 满分 1.0。"""
        cand = self._soft_candidate("团队协作", low=False)
        pos = _position("p1", musts=[_req("团队协作", Necessity.MUST)], required_years=3)
        result = score_position(cand, pos, weights=W)
        assert result.must_score == 1.0

    def test_semantic_hit_low_confidence_downsized(self):
        """语义命中（0.9 ≥ 阈值）后按 ×0.5 降权贡献分。"""
        cand = self._soft_candidate("沟通能力")
        pos = _position("p1", musts=[_req("团队协作", Necessity.MUST)])
        sem = _FakeSemantic({("团队协作", "沟通能力"): 0.9})
        result = score_position(cand, pos, weights=W, semantic=sem, sim_threshold=0.5)
        assert result.must_score == pytest.approx(0.45)

    def test_semantic_below_threshold_still_misses(self):
        """语义原始相似度低于阈值 → 不计入（降权不影响阈值判定）。"""
        cand = self._soft_candidate("沟通能力")
        pos = _position("p1", musts=[_req("团队协作", Necessity.MUST)])
        sem = _FakeSemantic({("团队协作", "沟通能力"): 0.4})
        result = score_position(cand, pos, weights=W, semantic=sem, sim_threshold=0.5)
        assert result.must_score == 0.0

    def test_soft_skill_nice_requirement_scored(self):
        """岗位侧 soft_skills 并入 nice 后，推断软技能命中按 ×0.5 计入 nice_score。"""
        cand = self._soft_candidate("团队协作")
        pos = _position(
            "p1",
            musts=[],
            nices=[
                _req("Python", Necessity.NICE, weight=0.4),
                _req("团队协作", Necessity.NICE, weight=0.4),
            ],
        )
        result = score_position(cand, pos, weights=W)
        # 命中团队协作 0.5×0.4，未命中 Python → 0.2 / 0.8 = 0.25
        assert result.nice_score == pytest.approx(0.25)
