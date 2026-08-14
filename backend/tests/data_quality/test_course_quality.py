"""课程质量评估单元测试（DA-M4-01，设计文档 §4.6）。

覆盖六维评分的各维度归一化边界与综合加权逻辑。
"""

from datetime import datetime, timedelta, timezone

from app.services.data_quality.course_quality import (
    RECOMMEND_MIN_SCORE,
    enrollment_score,
    evaluate_course,
    platform_authority,
    project_density,
    rating_score,
    recency_score,
    skill_coverage,
)

_TZ_CN = timezone(timedelta(hours=8))


def _days_ago(days: int) -> str:
    return (datetime.now(_TZ_CN).date() - timedelta(days=days)).isoformat()


class TestPlatformAuthority:
    def test_known_platforms(self):
        assert platform_authority("coursera") == 1.0
        assert platform_authority("edx") == 1.0
        assert platform_authority("icourse163") == 0.7

    def test_unknown_platform_neutral(self):
        assert platform_authority("unknown") == 0.5
        assert platform_authority("") == 0.5

    def test_case_insensitive(self):
        assert platform_authority("Coursera") == 1.0


class TestRatingScore:
    def test_full_rating(self):
        assert rating_score(5.0) == 1.0

    def test_half_rating(self):
        assert rating_score(2.5) == 0.5

    def test_missing_rating_neutral(self):
        assert rating_score(0.0) == 0.5
        assert rating_score(0) == 0.5

    def test_negative_clamped(self):
        assert rating_score(-1) == 0.5

    def test_invalid_input(self):
        assert rating_score("N/A") == 0.5


class TestEnrollmentScore:
    def test_cap_at_100k(self):
        assert enrollment_score(200_000) == 1.0

    def test_zero_or_missing_neutral(self):
        """缺失/0（爬虫未解析）取中性 0.5——与 rating 口径一致，避免数据缺口双重惩罚。"""
        assert enrollment_score(0) == 0.5
        assert enrollment_score(None) == 0.5

    def test_monotonic(self):
        assert enrollment_score(10_000) > enrollment_score(1_000)


class TestRecencyScore:
    def test_within_two_years_full(self):
        assert recency_score(_days_ago(365)) == 1.0

    def test_exactly_two_years(self):
        assert recency_score(_days_ago(365 * 2)) == 1.0

    def test_five_years_floor(self):
        assert recency_score(_days_ago(365 * 5)) == 0.5
        assert recency_score(_days_ago(365 * 8)) == 0.5

    def test_missing_date_neutral(self):
        assert recency_score(None) == 0.5
        assert recency_score("") == 0.5

    def test_invalid_date_neutral(self):
        assert recency_score("not-a-date") == 0.5


class TestSkillCoverage:
    def test_all_whitelist(self):
        assert skill_coverage(["Python", "Java"]) == 1.0

    def test_mixed(self):
        # "Python" 命中白名单，噪音词不命中 → 0.5
        assert skill_coverage(["Python", "发音纠正打卡"]) == 0.5

    def test_no_skills(self):
        assert skill_coverage([]) == 0.0


class TestProjectDensity:
    def test_project_keywords(self):
        # 命中"实战""项目""构建"3 个关键词 → 3/5 = 0.6
        assert project_density("包含实战项目，从构建到部署") == 0.6

    def test_saturation_at_five_keywords(self):
        assert project_density("实战项目开发案例实现实训 12345") == 1.0

    def test_no_keywords(self):
        assert project_density("本课程介绍基础概念") == 0.0

    def test_empty_description(self):
        assert project_density("") == 0.0
        assert project_density(None) == 0.0


class TestEvaluateCourse:
    def test_high_quality_recommended(self):
        """国际平台 + 高评分 + 高注册 + 新课程 + 白名单技能 + 实战简介 → 推荐。"""
        result = evaluate_course(
            {
                "title": "Python 数据科学实战",
                "platform": "coursera",
                "rating": 4.8,
                "enrollment": 80_000,
                "start_date": _days_ago(180),
                "skills": ["Python", "Pandas"],
                "description": "包含多个实战项目，手把手构建数据管线",
            }
        )
        assert result.quality_score >= RECOMMEND_MIN_SCORE
        assert result.recommended is True

    def test_low_quality_not_recommended(self):
        """国内平台 + 无评分 + 无注册 + 老课程 + 无技能/简介 → 不入推荐池。"""
        result = evaluate_course(
            {
                "title": "老课程",
                "platform": "icourse163",
                "rating": 0,
                "enrollment": 0,
                "start_date": _days_ago(365 * 6),
                "skills": [],
                "description": "",
            }
        )
        assert result.recommended is False
        assert result.quality_score < RECOMMEND_MIN_SCORE

    def test_weighted_sum_matches(self):
        """六维加权：0.25/0.20/0.15/0.20/0.10/0.10 与文档 §4.6 对齐。"""
        course = {
            "platform": "coursera",
            "rating": 5.0,
            "enrollment": 200_000,
            "start_date": _days_ago(100),
            "skills": ["Python"],
            "description": "实战项目开发案例实现实训构建",
        }
        r = evaluate_course(course)
        expected = (
            0.25 * r.platform_score
            + 0.20 * r.rating_score
            + 0.15 * r.enrollment_score
            + 0.20 * r.recency_score
            + 0.10 * r.skill_coverage_score
            + 0.10 * r.project_density_score
        )
        assert r.quality_score == round(expected, 4)
        # 全满分维度 → 综合分 1.0
        assert r.quality_score == 1.0

    def test_platform_field_copied(self):
        result = evaluate_course({"title": "t", "platform": "edx"})
        assert result.title == "t"
        assert result.platform == "edx"
