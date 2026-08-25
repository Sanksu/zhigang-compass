"""JD 级证据精排（阶段 B）单元测试——纯函数层，无 DB。"""

from app.services.matching.jd_rerank import (
    _coverage_score,
    _jd_skill_names,
    rank_jds_for_position,
    enrich_with_jd_evidence,
)


def _jd_row(snap, source="zhilian", url="https://x/1"):
    return {"snapshot": snap, "source": source, "source_url": url}


def _ext(title, musts, nices=None, position_name="后端开发工程师"):
    return {
        "normalized_position": position_name,
        "title": title,
        "extraction": {
            "skills": [{"name": s} for s in musts],
            "requirements": [{"skill_name": s} for s in (nices or [])],
        },
    }


class TestSkillNames:
    def test_skills_and_requirements_separated(self):
        extraction = {
            "skills": [{"name": "Java"}, {"name": "Spring"}],
            "requirements": [{"skill_name": "MySQL"}, {"name": "Redis"}],
        }
        musts, nices = _jd_skill_names(extraction)
        assert musts == ["Java", "Spring"]
        assert set(nices) == {"MySQL", "Redis"}

    def test_str_skills_tolerated(self):
        musts, nices = _jd_skill_names({"skills": ["Python"], "requirements": []})
        assert musts == ["Python"]


class TestCoverageScore:
    def test_full_must_hit(self):
        assert _coverage_score({"Java", "Spring"}, ["Java", "Spring"], []) == 1.0

    def test_partial_must(self):
        assert _coverage_score({"Java"}, ["Java", "Spring"], []) == 0.5

    def test_nice_half_weight(self):
        """nice 半计入：2 must 全中 + 2 nice 全中 = (2+1)/3。"""
        cand = {"Java", "Spring", "MySQL", "Redis"}
        got = _coverage_score(cand, ["Java", "Spring"], ["MySQL", "Redis"])
        assert got == 3.0 / 3.0

    def test_unknown_jd_no_skills_zero(self):
        assert _coverage_score({"Java"}, [], []) == 0.0


class TestRankJds:
    def test_filters_by_position_name_and_orders(self):
        rows = [
            _jd_row(_ext("JD-A", ["Java", "Spring"], position_name="后端开发工程师")),
            _jd_row(_ext("JD-B", ["Python"], position_name="后端开发工程师")),
            _jd_row(_ext("JD-C", ["Java"], position_name="其它岗位")),  # 不同岗位名 → 排除
        ]
        out = rank_jds_for_position(rows, "后端开发工程师", ["Java", "Spring"], k=2)
        assert len(out) == 2
        # JD-A 覆盖 1.0 在前
        assert out[0]["jd_title"] == "JD-A"
        assert out[0]["coverage"] == 1.0
        assert out[0]["hit_skills"] == ["Java", "Spring"]
        assert out[1]["jd_title"] == "JD-B"

    def test_k_caps(self):
        rows = [_jd_row(_ext(f"JD-{i}", ["Java"], position_name="后端开发工程师")) for i in range(5)]
        out = rank_jds_for_position(rows, "后端开发工程师", ["Java"], k=2)
        assert len(out) == 2

    def test_skips_no_must_no_hit(self):
        rows = [_jd_row(_ext("JD-X", [], position_name="后端开发工程师"))]
        out = rank_jds_for_position(rows, "后端开发工程师", ["Java"])
        assert out == []

    def test_fields_shape(self):
        rows = [_jd_row(_ext("JD-Y", ["Go"], position_name="后端开发工程师"), source="boss", url="https://u")]
        out = rank_jds_for_position(rows, "后端开发工程师", ["Go"])
        assert out[0]["source"] == "boss"
        assert out[0]["source_url"] == "https://u"
        assert "coverage" in out[0] and "must_total" in out[0]


class TestEnrich:
    def test_attaches_evidence_to_matching_item(self):
        results = [
            {"position_id": "pos_1", "position_name": "后端开发工程师", "total_score": 0.8},
            {"position_id": "pos_2", "position_name": "前端开发工程师", "total_score": 0.6},
        ]
        rows_by_pos = {
            "后端开发工程师": [_jd_row(_ext("JD-A", ["Java"], position_name="后端开发工程师"))],
            # 前端岗位无 JD → 空证据
        }
        enrich_with_jd_evidence(results, rows_by_pos, ["Java"], k=2)
        assert len(results[0]["jd_evidence"]) == 1
        assert results[0]["jd_evidence"][0]["jd_title"] == "JD-A"
        assert results[1]["jd_evidence"] == []

    def test_no_rows_no_key_break(self):
        results = [{"position_id": "p", "position_name": "X", "total_score": 0.5}]
        enrich_with_jd_evidence(results, {}, ["Java"])
        assert results[0]["jd_evidence"] == []