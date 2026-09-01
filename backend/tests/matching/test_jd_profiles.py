"""阶段 C 内核测试：JD → PositionProfile、预筛、岗位聚合（纯函数，无 DB）。"""

from app.services.matching.jd_profiles import jd_profile_from_snapshot, rough_select
from app.services.matching.schemas import MatchResult
from app.services.matching.jd_aggregate import aggregate_jd_scores


def _snap(title, skills, reqs=None, position_name="后端开发工程师", years=3, scenarios=None):
    return {
        "title": title,
        "normalized_position": position_name,
        "crawled_at": "2026-08-25",
        "extraction": {
            "skills": [{"name": s} for s in skills],
            "requirements": [{"skill_name": s} for s in (reqs or [])],
            # P0 协议：经验年限由 extraction.experience_range.min_years 承载（原 required_years 从未被抽取）
            "experience_range": {"min_years": years},
            "industry": "IT",
            "typical_scenarios": scenarios or [],
        },
    }


class TestJdProfile:
    def test_builds_must_nice(self):
        prof = jd_profile_from_snapshot(_snap("JD-A", ["Java", "Spring"], reqs=["MySQL"]), "42")
        assert prof.position_id == "42"
        assert [s.skill_name for s in prof.must_skills] == ["Java", "Spring"]
        assert [s.skill_name for s in prof.nice_skills] == ["MySQL"]
        assert prof.required_years == 3
        assert prof.industry == "IT"
        assert prof.soft_requirements == []

    def test_soft_skill_isolated(self):
        snap = _snap("JD-S", ["Python"])
        snap["extraction"]["skills"] = [{"name": "沟通能力", "category": "soft-skills"}]
        prof = jd_profile_from_snapshot(snap, "1")
        assert [s.skill_name for s in prof.must_skills] == []
        assert len(prof.soft_requirements) == 1
        assert prof.soft_requirements[0].skill_name == "沟通能力"

    def test_no_skills_returns_none(self):
        assert jd_profile_from_snapshot({"extraction": {}}, "1") is None
        assert jd_profile_from_snapshot({}, "1") is None

    def test_years_from_experience_range_min_years(self):
        """P0：required_years 取自 extraction.experience_range.min_years。"""
        snap = _snap("JD-Y", ["Python"], years=5)
        prof = jd_profile_from_snapshot(snap, "1")
        assert prof.required_years == 5

    def test_missing_experience_range_yields_none_years(self):
        """P0：无经验区间 → required_years=None（表示无准入年限，≠0）。"""
        snap = _snap("JD-N", ["Python"], years=3)
        snap["extraction"].pop("experience_range", None)
        prof = jd_profile_from_snapshot(snap, "1")
        assert prof.required_years is None

    def test_invalid_min_years_yields_none(self):
        """P0：min_years 为 0/负数/非数值 → None（不误判为有年限）。"""
        for bad in (0, -1, "abc", None):
            snap = _snap("JD-B", ["Python"])
            snap["extraction"]["experience_range"] = {"min_years": bad}
            prof = jd_profile_from_snapshot(snap, "1")
            assert prof.required_years is None, f"min_years={bad}"


class TestRoughSelect:
    def test_orders_by_hits(self):
        profs = [
            jd_profile_from_snapshot(_snap("JD-A", ["Java", "Spring"]), "1"),
            jd_profile_from_snapshot(_snap("JD-B", ["Python"]), "2"),
            jd_profile_from_snapshot(_snap("JD-C", ["Java"]), "3"),
        ]
        out = rough_select(profs, ["Java", "Spring"], k=2)
        assert [p.position_id for p in out] == ["1", "3"]  # JD-A 命中2 > JD-C 命中1

    def test_zero_hit_keeps_fallback(self):
        profs = [
            jd_profile_from_snapshot(_snap("JD-A", ["Rust"]), "1"),
            jd_profile_from_snapshot(_snap("JD-B", ["Zig"]), "2"),
        ]
        out = rough_select(profs, ["Java"], k=2)
        assert len(out) == 2  # 0 命中保留兜底


class TestAggregate:
    def _result(self, jd_id, name, score):
        return MatchResult(
            position_id=jd_id, position_name=name, total_score=score,
            nice_score=0.5, exp_score=0.5,
            matched_must=["Java"], missing_must=[],
            summary=f"摘要{name}", unqualified=False,
        )

    def test_groups_by_position_and_best_jd(self):
        jd_position = {"1": "后端开发工程师", "2": "后端开发工程师", "3": "前端开发工程师"}
        scored = [
            self._result("1", "JD-A", 0.9),
            self._result("2", "JD-B", 0.7),
            self._result("3", "JD-C", 0.8),
        ]
        out = aggregate_jd_scores(scored, jd_position, top_n=2)
        assert [r["position_name"] for r in out] == ["后端开发工程师", "前端开发工程师"]
        assert out[0]["total_score"] == 0.9  # 组内最高分
        assert len(out[0]["jd_evidence"]) == 2  # 组内 Top-2 JD 证据
        assert out[0]["jd_evidence"][0]["jd_id"] == "1"

    def test_no_position_group_excluded(self):
        """无岗位名归属的 JD（normalized_position 空）不参与岗位聚合（被排除）。"""
        out = aggregate_jd_scores([self._result("9", "JD-X", 0.5)], {"9": ""}, top_n=1)
        assert out == []

class TestExperienceRangeMapping:
    """2026-09-01 修复：experience_range{min_years} → required_years 映射。

    抽取 schema 输出 experience_range 而引擎读 required_years，字段名错位
    导致 exp_score 恒满分（全库填充率 0）。
    """

    def _snapshot(self, extraction: dict) -> dict:
        return {
            "title": "测试工程师",
            "normalized_position": "测试工程师",
            "extraction": {
                "skills": [{"name": "Python", "necessity": "must", "level": "熟练"}],
                **extraction,
            },
        }

    def test_min_years_maps_to_required_years(self):
        snap = self._snapshot({"experience_range": {"min_years": 5, "max_years": None}})
        prof = jd_profile_from_snapshot(snap, "jd-1")
        assert prof.required_years == 5

    def test_range_takes_lower_bound(self):
        snap = self._snapshot({"experience_range": {"min_years": 3, "max_years": 5}})
        prof = jd_profile_from_snapshot(snap, "jd-1")
        assert prof.required_years == 3

    def test_no_range_returns_none(self):
        snap = self._snapshot({})
        prof = jd_profile_from_snapshot(snap, "jd-1")
        assert prof.required_years is None

    def test_zero_min_years_treated_as_none(self):
        snap = self._snapshot({"experience_range": {"min_years": 0, "max_years": None}})
        prof = jd_profile_from_snapshot(snap, "jd-1")
        assert prof.required_years is None
