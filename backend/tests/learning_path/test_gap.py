"""差距分析三态单元测试（AL-M4-03，设计文档 §9.5）。"""

from app.services.learning_path.gap import analyze_gaps
from app.services.learning_path.schemas import GapType
from app.services.matching.schemas import (
    CandidateProfile,
    CandidateSkill,
    Necessity,
    PositionProfile,
    SkillRequirement,
)


def _candidate(skills: list[tuple[str, int]]) -> CandidateProfile:
    return CandidateProfile(
        user_id="u1",
        skills=[
            CandidateSkill(skill_id=name, skill_name=name, proficiency=proficiency)
            for name, proficiency in skills
        ],
    )


def _req(name: str, necessity: Necessity = Necessity.MUST, weight: float = 1.0, proficiency=None, is_soft: bool = False):
    return SkillRequirement(
        skill_id=name, skill_name=name, necessity=necessity, weight=weight,
        proficiency=proficiency, is_soft=is_soft,
    )


def _position(musts: list, nices: list | None = None, softs: list | None = None) -> PositionProfile:
    return PositionProfile(
        position_id="p1", name="p1", must_skills=musts,
        nice_skills=nices or [], soft_requirements=softs or [],
    )


class TestSoftSkillTag:
    """软技能打标透传（SkillRequirement.is_soft → GapSkill.is_soft，仅展示不影响三态判定）。"""

    def test_is_soft_passthrough(self):
        cand = _candidate([])
        gaps = analyze_gaps(
            cand,
            _position([_req("PyTorch"), _req("沟通能力", necessity=Necessity.NICE, is_soft=True)]),
        )
        by_name = {g.skill: g for g in gaps}
        assert by_name["沟通能力"].is_soft is True
        assert by_name["PyTorch"].is_soft is False

    def test_soft_channel_enters_gaps(self):
        """独立通道 soft_requirements（不参与评分）同样进入差距列表供展示。"""
        cand = _candidate([])
        gaps = analyze_gaps(
            cand,
            _position(
                [_req("PyTorch")],
                softs=[_req("沟通能力", necessity=Necessity.NICE, weight=0.4, is_soft=True)],
            ),
        )
        by_name = {g.skill: g for g in gaps}
        assert by_name["沟通能力"].is_soft is True
        assert by_name["沟通能力"].gap_type == GapType.MISSING

    def test_is_soft_not_affect_gap_type(self):
        """软技能缺失仍按 necessity 定优先级（nice → medium），不因打标改变三态。"""
        cand = _candidate([])
        gaps = analyze_gaps(
            cand, _position([], nices=[_req("责任心", necessity=Necessity.NICE, is_soft=True)])
        )
        assert len(gaps) == 1
        assert gaps[0].gap_type == GapType.MISSING
        assert gaps[0].priority == "medium"


class TestGapTypes:
    def test_missing_skill(self):
        """候选人缺失必备技能 → missing，优先级 high。"""
        cand = _candidate([("Python", 2)])
        gaps = analyze_gaps(cand, _position([_req("Java", weight=0.8)]))
        assert len(gaps) == 1
        assert gaps[0].gap_type == GapType.MISSING
        assert gaps[0].necessity == "must"
        assert gaps[0].priority == "high"
        assert gaps[0].current_proficiency is None

    def test_matched_skill(self):
        cand = _candidate([("Python", 3)])
        gaps = analyze_gaps(cand, _position([_req("Python")]))
        assert gaps[0].gap_type == GapType.MATCHED
        assert gaps[0].priority == "low"
        assert gaps[0].current_proficiency == "精通"

    def test_weak_when_proficiency_below_required(self):
        """候选人含技能但熟练度低于岗位期望 → weak。"""
        cand = _candidate([("Python", 1)])  # 了解
        gaps = analyze_gaps(cand, _position([_req("Python", proficiency="中级")]))
        assert gaps[0].gap_type == GapType.WEAK
        assert gaps[0].priority == "medium"
        assert gaps[0].current_proficiency == "了解"
        assert gaps[0].required_proficiency == "中级"

    def test_matched_when_proficiency_meets_required(self):
        cand = _candidate([("Python", 2)])  # 熟悉 ≥ 中级
        gaps = analyze_gaps(cand, _position([_req("Python", proficiency="中级")]))
        assert gaps[0].gap_type == GapType.MATCHED

    def test_alias_requirement_uses_same_semantics_as_scoring(self):
        # 精通规范为高级：候选人“熟悉”(2) 不足，须显示 weak。
        cand = _candidate([("Python", 2)])
        gaps = analyze_gaps(cand, _position([_req("Python", proficiency="精通")]))
        assert gaps[0].gap_type == GapType.WEAK
        assert gaps[0].required_proficiency == "精通"

    def test_expert_margin_matches_scoring_matrix(self):
        # 既定矩阵中“专家 × 精通”=0.85，gap 应同样显示 weak。
        cand = _candidate([("Python", 3)])
        gaps = analyze_gaps(cand, _position([_req("Python", proficiency="专家")]))
        assert gaps[0].gap_type == GapType.WEAK

    def test_unknown_requirement_is_weak_not_silently_matched(self):
        """非空未知等级不得在 gap 中被视为完全满足。"""
        cand = _candidate([("Python", 3)])
        gaps = analyze_gaps(cand, _position([_req("Python", proficiency="未知")]))
        assert gaps[0].gap_type == GapType.WEAK

    def test_no_required_level_is_not_weak(self):
        """岗位未声明期望熟练度 → 不判 weak。"""
        cand = _candidate([("Python", 1)])
        gaps = analyze_gaps(cand, _position([_req("Python")]))
        assert gaps[0].gap_type == GapType.MATCHED

    def test_missing_nice_priority_medium(self):
        cand = _candidate([])
        gaps = analyze_gaps(cand, _position([], nices=[_req("Go", necessity=Necessity.NICE)]))
        assert gaps[0].gap_type == GapType.MISSING
        assert gaps[0].priority == "medium"

    def test_weak_nice_priority_low(self):
        cand = _candidate([("Go", 1)])
        gaps = analyze_gaps(
            cand, _position([], nices=[_req("Go", necessity=Necessity.NICE, proficiency="高级")])
        )
        assert gaps[0].gap_type == GapType.WEAK
        assert gaps[0].priority == "low"


class TestGapSorting:
    def test_missing_before_weak_before_matched(self):
        cand = _candidate([("Python", 1), ("Go", 3)])
        pos = _position(
            musts=[
                _req("Python", weight=0.5, proficiency="专家"),  # weak
                _req("Java", weight=0.9),  # missing
                _req("Go", weight=0.4),  # matched
            ]
        )
        gaps = analyze_gaps(cand, pos)
        types = [g.gap_type for g in gaps]
        assert types == [GapType.MISSING, GapType.WEAK, GapType.MATCHED]

    def test_weight_desc_within_same_type(self):
        cand = _candidate([])
        pos = _position(
            musts=[_req("A", weight=0.3), _req("B", weight=0.9), _req("C", weight=0.6)]
        )
        gaps = analyze_gaps(cand, pos)
        assert [g.skill for g in gaps] == ["B", "C", "A"]

    def test_weight_takes_precedence_over_gap_type(self):
        """设计文档 §9.5：weight DESC 优先，gap_type 仅作次级排序。"""
        cand = _candidate([("Go", 3), ("Python", 1)])
        pos = _position(
            musts=[
                _req("Go", weight=0.9),  # matched，但 weight 最高 → 排最前
                _req("Java", weight=0.8),  # missing
                _req("Python", weight=0.7, proficiency="专家"),  # weak
            ]
        )
        gaps = analyze_gaps(cand, pos)
        assert [g.skill for g in gaps] == ["Go", "Java", "Python"]


class TestGapDataUpgrade:
    """数据升级（task 2.x）：demand/trend/roi/evidence/high_roi。"""

    def _req_src(self, name: str, source_count: int, **kw):
        return SkillRequirement(
            skill_id=name, skill_name=name, necessity=Necessity.MUST,
            weight=1.0, source_count=source_count, **kw,
        )

    def test_demand_normalized_from_source_count(self, monkeypatch):
        """demand = min(1, source_count/20)；无源按 1。"""
        monkeypatch.setattr("app.services.learning_path.gap._trend_signal", lambda sid, sc: 0.0)
        gaps = analyze_gaps(_candidate([]), _position([self._req_src("Java", 20)]))
        assert gaps[0].demand == 1.0
        gaps = analyze_gaps(_candidate([]), _position([self._req_src("Go", 5)]))
        assert gaps[0].demand == 0.25

    def test_trend_is_continuous_diffusion(self, monkeypatch):
        """trend 连续值：岗位扩散 + 跨源扩散等权合成（非两档）。"""
        # 无关联岗位（_position_count→0）仅跨源项：source_count=10 → 0.5×0+0.5×0.5=0.25
        monkeypatch.setattr("app.services.learning_path.gap._position_count", lambda sid: 0)
        gaps = analyze_gaps(_candidate([]), _position([self._req_src("Java", 10)]))
        assert gaps[0].trend == 0.25
        # 关联 10 岗位 + 20 源 → 0.5×1 + 0.5×1 = 1.0
        monkeypatch.setattr("app.services.learning_path.gap._position_count", lambda sid: 10)
        gaps = analyze_gaps(_candidate([]), _position([self._req_src("Java", 20)]))
        assert gaps[0].trend == 1.0
        # 关联 5 岗位 + 5 源 → 0.5×0.5 + 0.5×0.25 = 0.375
        monkeypatch.setattr("app.services.learning_path.gap._position_count", lambda sid: 5)
        gaps = analyze_gaps(_candidate([]), _position([self._req_src("Java", 5)]))
        assert gaps[0].trend == 0.375

    def test_roi_and_high_roi_top3(self, monkeypatch):
        """roi=(demand×(trend+1))/cost；真缺口按 ROI 取 Top3 打标，matched 不打标。"""
        monkeypatch.setattr("app.services.learning_path.gap._trend_signal", lambda sid, sc: 0.0)
        # 高 cost 技能 ROI 低
        cand = _candidate([("Python", 3), ("Go", 3)])
        pos = _position(
            musts=[
                self._req_src("Python", 20),  # matched → 不打标
                self._req_src("Java", 5),     # missing
                self._req_src("Rust", 5),     # missing
                self._req_src("Kotlin", 5),   # missing
            ]
        )
        gaps = analyze_gaps(cand, pos)
        high = [g.skill for g in gaps if g.high_roi]
        assert len(high) == 3  # 仅缺口的 Top3
        assert "Python" not in high  # matched 不打标
        # matched 项 roi 已算（供展示），high_roi=False
        python = next(g for g in gaps if g.skill == "Python")
        assert python.roi is not None and python.high_roi is False

    def test_evidence_role_and_text(self):
        """evidence 含 JD 要求 + 简历现状。"""
        cand = _candidate([("Python", 1)])
        gaps = analyze_gaps(cand, _position([_req("Python", proficiency="中级")]))
        roles = [(e.role, e.text) for e in gaps[0].evidence]
        assert roles[0] == ("jd", "JD 要求：中级")
        assert roles[1][0] == "resume" and "简历" in roles[1][1]
