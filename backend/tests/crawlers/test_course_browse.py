"""课程源浏览页增强单元测试（08-16：空词模式产出提升）。

覆盖：
- coursera：career-academy 角色页（非课程）过滤
- coursera：/learn/ 与 /specializations/ 卡片正常产出
- coursera：关键词本地过滤（08-22：/browse 服务端无视 query 参数，
  定向采集必须本地过滤，否则热门课全量误入）
"""

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))

from parsel import Selector
from crawlers.spiders.coursera import CourseraSpider


def _make_spider(keywords: list[str] | None = None) -> CourseraSpider:
    spider = CourseraSpider.__new__(CourseraSpider)
    spider.name = "coursera"
    spider.platform = "coursera"
    spider.keywords = keywords or []
    spider.max_pages = 3
    spider.download_delay = 15
    return spider


def _card(href: str, title: str = "Course Title") -> Selector:
    return Selector(
        text=(
            '<li class="cds-grid-item"><a class="cds-CommonCard-titleLink" '
            f'href="{href}">{title}</a>'
            '<div class="cds-ProductCard-partnerNames">Stanford</div></li>'
        )
    )


class TestCourseraBrowse:
    def test_career_academy_role_filtered(self):
        """浏览页职业角色页（非课程）被过滤。"""
        spider = _make_spider()
        item = spider._card_to_item(
            _card("/career-academy/roles/data-scientist?level=beginner"),
            {"keyword": ""},
        )
        assert item is None

    def test_learn_course_parsed(self):
        """/learn/{slug} 课程正常产出。"""
        spider = _make_spider()
        item = spider._card_to_item(_card("/learn/foundations-of-cybersecurity"), {"keyword": ""})
        assert item is not None
        assert item["source_id"] == "foundations-of-cybersecurity"

    def test_specialization_parsed(self):
        """/specializations/{slug} 专项课程正常产出。"""
        spider = _make_spider()
        item = spider._card_to_item(_card("/specializations/deep-learning"), {"keyword": ""})
        assert item is not None
        assert item["source_id"] == "deep-learning"


class TestKeywordFilter:
    """08-22 关键词本地过滤：/browse 无视 query 参数的兜底防线。"""

    def test_keyword_hit_title_kept(self):
        """标题含关键词（词边界命中）保留。"""
        spider = _make_spider(["Airflow"])
        card = Selector(
            text=(
                '<li class="cds-grid-item"><a class="cds-CommonCard-titleLink" '
                'href="/learn/etl-airflow">Building ETL Pipelines with Airflow</a></li>'
            )
        )
        item = spider._card_to_item(card, {"keyword": "Airflow"})
        assert item is not None
        assert item["category"] == "Airflow"

    def test_keyword_unrelated_blocked(self):
        """标题/院校均不含关键词的热门课拦截（当日实证误入库场景）。"""
        spider = _make_spider(["PostgreSQL"])
        item = spider._card_to_item(
            _card("/learn/python-for-everybody", title="Python for Everybody"),
            {"keyword": "PostgreSQL"},
        )
        assert item is None

    def test_keyword_word_boundary_no_substring_false_positive(self):
        """词边界匹配："Air" 不命中 "Hair"（子串匹配会误报）。"""
        assert not CourseraSpider._keyword_hit("air", "Hair Care Basics")
        assert CourseraSpider._keyword_hit("air", "AWS Cloud Air Transport")

    def test_keyword_cjk_substring(self):
        """CJK 关键词子串匹配。"""
        assert CourseraSpider._keyword_hit("机器学习", "Python机器学习入门实战")

    def test_empty_keyword_passes_all(self):
        """空关键词 = 热门课全量模式，全部通过。"""
        assert CourseraSpider._keyword_hit("", "任意标题")
