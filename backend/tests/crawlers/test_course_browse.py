"""课程源浏览页增强单元测试（08-16：空词模式产出提升）。

覆盖：
- coursera：career-academy 角色页（非课程）过滤
- coursera：/learn/ 与 /specializations/ 卡片正常产出
"""

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))

from parsel import Selector
from crawlers.spiders.coursera import CourseraSpider


def _make_spider():
    spider = CourseraSpider.__new__(CourseraSpider)
    spider.name = "coursera"
    spider.platform = "coursera"
    spider.keywords = []
    spider.max_pages = 3
    spider.download_delay = 15
    return spider


def _card(href: str) -> Selector:
    return Selector(
        text=(
            '<li class="cds-grid-item"><a class="cds-CommonCard-titleLink" '
            f'href="{href}">Course Title</a>'
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
