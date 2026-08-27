"""算法口径四项回归测试（第六轮审查 §三 算法口径，交张恺天复核）。

1. 岗位多样性配额：同族 JD 不再占满召回席位（diversify_by_position）
2. rough_select 规范名口径：与 engine._canonical_name 一致（Golang→Go）
3. hit_count 统一 must+nice：MatchResult.matched_nice 暴露 + 聚合层口径
4. 混布快照 relation 口径：见 tests/discovery（快照级 any() 判定）
"""

from app.services.matching.jd_profiles import diversify_by_position, rough_select
from app.services.matching.schemas import (
    CandidateProfile,
    CandidateSkill,
    PositionProfile,
    SkillRequirement,
)


def _pos(pid: str, name: str, musts: list[str], nices: list[str] | None = None) -> PositionProfile:
    return PositionProfile(
        position_id=pid, name=name,
        must_skills=[SkillRequirement(skill_id=f"m_{m}", skill_name=m, necessity="must") for m in musts],
        nice_skills=[SkillRequirement(skill_id=f"n_{n}", skill_name=n, necessity="nice") for n in (nices or [])],
    )


class TestDiversifyByPosition:
    def test_single_family_no_longer_dominates_pool(self):
        """10 个同族 JD + 3 个外族 JD：配额后外族必须占席（修复前全被挤掉）。"""
        pool = [_pos(f"jd_a{i}", f"JD-A{i}", ["Python"]) for i in range(10)]
        pool += [_pos(f"jd_b{i}", f"JD-B{i}", ["Python"]) for i in range(3)]
        jd_position = {f"jd_a{i}": "后端工程师" for i in range(10)}
        jd_position.update({f"jd_b{i}": "算法工程师" for i in range(3)})

        out = diversify_by_position(pool, jd_position, k=10, top_n=5)

        families = {jd_position[p.position_id] for p in out}
        assert "算法工程师" in families  # 外族不再被挤掉
        assert len(out) == 10

    def test_capacity_and_order_preserved(self):
        """池内总数不超 k；组内保持召回相似度序。"""
        pool = (
            [_pos(f"a{i}", f"A{i}", ["Go"]) for i in range(6)]
            + [_pos(f"b{i}", f"B{i}", ["Go"]) for i in range(6)]
        )
        jd_position = {**{f"a{i}": "岗位A" for i in range(6)},
                       **{f"b{i}": "岗位B" for i in range(6)}}
        out = diversify_by_position(pool, jd_position, k=8, top_n=4)
        assert len(out) == 8
        a_ids = [p.position_id for p in out if p.position_id.startswith("a")]
        assert a_ids == ["a0", "a1", "a2", "a3"]  # cap = max(2, 8//4)=2，两轮取 4

    def test_no_position_jds_skipped_and_empty_short_circuits(self):
        assert diversify_by_position([], {}, k=10, top_n=5) == []
        pool = [_pos("x1", "X1", ["Go"]), _pos("y1", "Y1", ["Go"])]
        out = diversify_by_position(pool, {"x1": "岗位X"}, k=10, top_n=5)
        assert [p.position_id for p in out] == ["x1"]  # 无归属 JD 不占配额席


class TestRoughSelectCanonicalCaliber:
    def test_alias_variants_counted_via_canonical_name(self):
        """Golang（候选人）↔ Go（JD）：规范名口径下应命中（旧 strip().lower() 口径 miss）。"""
        jd = _pos("jd1", "Go 岗位", ["Go"])
        out = rough_select([jd], ["Golang"], k=5)
        assert out and out[0].position_id == "jd1"

    def test_zero_hit_fallback_kept(self):
        jd = _pos("jd1", "Rust 岗位", ["Rust"])
        out = rough_select([jd], ["Python"], k=5)
        assert len(out) == 1  # 全 0 命中兜底保留


class TestMatchedNiceExposure:
    def test_matched_nice_populated_and_aggregate_hit_count_unified(self):
        """matched_nice 暴露 + aggregate hit_count = must+nice（与 jd_rerank 同口径）。"""
        from app.services.matching.engine import score_position
        from app.services.matching.jd_aggregate import aggregate_jd_scores

        position = _pos("jd1", "JD1", musts=["Python"], nices=["Docker"])
        candidate = CandidateProfile(
            user_id="u1",
            skills=[CandidateSkill(skill_id="s1", skill_name="Python", proficiency=2),
                    CandidateSkill(skill_id="s2", skill_name="Docker", proficiency=1)],
        )
        r = score_position(candidate, position)
        assert r.matched_must == ["Python"]
        assert r.matched_nice == ["Docker"]

        aggregated = aggregate_jd_scores([r], {"jd1": "后端工程师"}, top_n=5)
        assert aggregated[0]["jd_evidence"][0]["hit_count"] == 2  # must+nice
