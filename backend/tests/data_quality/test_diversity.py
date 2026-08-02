"""数据多样性指标单元测试（DA-M4-02）。"""

import pytest

from app.services.data_quality.diversity import (
    course_diversity,
    dedup_stats,
    position_diversity,
    source_distribution,
)


class TestSourceDistribution:
    def test_counts_by_source(self):
        rows = [{"source": "boss"}, {"source": "boss"}, {"source": "zhilian"}, {"source": None}]
        result = source_distribution(rows)
        assert result[0] == {"source": "boss", "count": 2}
        assert {"source": "zhilian", "count": 1} in result
        # 空 source 归为 unknown
        assert {"source": "unknown", "count": 1} in result


class TestDedupStats:
    def test_no_duplicates(self):
        rows = [{"fingerprint": "a"}, {"fingerprint": "b"}, {"fingerprint": "c"}]
        r = dedup_stats(rows)
        assert r == {"total": 3, "unique": 3, "duplicates": 0, "duplicate_rate": 0.0}

    def test_with_duplicates(self):
        rows = [{"fingerprint": "a"}, {"fingerprint": "a"}, {"fingerprint": "b"}]
        r = dedup_stats(rows)
        assert r["unique"] == 2
        assert r["duplicates"] == 1
        assert r["duplicate_rate"] == pytest.approx(0.3333)  # 函数输出 4 位小数

    def test_empty(self):
        assert dedup_stats([])["duplicate_rate"] == 0.0


class TestPositionDiversity:
    def test_unique_positions_and_top(self):
        items = [
            {"position_name": "算法工程师", "skills": ["Python", "机器学习"]},
            {"position_name": "算法工程师", "skills": ["Python", "深度学习"]},
            {"position_name": "前端开发工程师", "skills": ["JavaScript"]},
            {"position_name": "", "skills": []},  # 空岗位名不计
        ]
        r = position_diversity(items, top_n=5)
        assert r["total_positions"] == 3
        assert r["unique_positions"] == 2
        assert r["top_positions"][0] == {"name": "算法工程师", "count": 2}
        # 技能提及 5 次：Python×2 + 机器学习 + 深度学习 + JavaScript
        assert r["skill_mentions"] == 5
        assert r["unique_skills"] == 4

    def test_cr10_concentration(self):
        """CR10 = Top-10 技能提及占比；技能全部相同 → 1.0。"""
        items = [
            {"position_name": "P", "skills": ["Python", "Python", "Python", "Python", "Python"]},
        ]
        r = position_diversity(items)
        assert r["cr10"] == 1.0

    def test_avg_skills_per_position(self):
        items = [
            {"position_name": "A", "skills": ["x", "y"]},
            {"position_name": "B", "skills": []},
        ]
        r = position_diversity(items)
        assert r["avg_skills_per_position"] == 1.0


class TestCourseDiversity:
    def test_platforms_and_skill_tags(self):
        items = [
            {"platform": "coursera", "skills": ["Python", "机器学习"]},
            {"platform": "coursera", "skills": ["深度学习"]},
            {"platform": "icourse163", "skills": ["Python"]},
        ]
        r = course_diversity(items)
        assert r["total_courses"] == 3
        assert r["platforms"][0] == {"platform": "coursera", "count": 2}
        assert r["unique_skill_tags"] == 3

    def test_empty(self):
        r = course_diversity([])
        assert r["total_courses"] == 0
        assert r["unique_skill_tags"] == 0
