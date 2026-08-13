"""zhilian 爬虫 JD 采集上限单元测试（08-13：默认 200 条截断防超长运行）。

覆盖：
- max_results 参数解析（默认 200 / 显式传值）
- _bump_items 达到上限触发 CloseSpider（增量与降级路径同约束）
"""

import sys
from pathlib import Path

import pytest
from scrapy.exceptions import CloseSpider

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))

from crawlers.spiders.zhilian import ZhilianSpider


def _make_spider(**kwargs):
    """构造不启动网络的爬虫实例（start_requests 不被调用）。"""
    spider = ZhilianSpider.__new__(ZhilianSpider)
    spider.name = "zhilian"
    spider.platform = "zhilian"
    spider.keywords = kwargs.get("keywords") or ["Python"]
    spider.cities = kwargs.get("cities") or ["北京"]
    spider.history_days = int(kwargs.get("history_days") or 0)
    spider._max_pages = 50 if spider.history_days else 5
    spider._max_results = int(kwargs.get("max_results") or 200)
    spider._items_collected = 0
    return spider


class TestMaxResults:
    def test_default_max_results_200(self):
        """未传 max_results 时默认 200 条。"""
        s = _make_spider()
        assert s._max_results == 200

    def test_explicit_max_results(self):
        """显式传 max_results 生效。"""
        s = _make_spider(max_results="500")
        assert s._max_results == 500

    def test_history_backfill_still_bounded(self):
        """历史回爬（history_days）同样受默认 200 条上限约束。"""
        s = _make_spider(history_days="90")
        assert s._max_results == 200
        assert s._max_pages == 50

    def test_bump_items_closes_at_limit(self):
        """产出达到 max_results 条时关闭（>= 语义：正好产出上限条数）。"""
        s = _make_spider(max_results="3")
        s._bump_items()
        s._bump_items()
        assert s._items_collected == 2
        # 第 3 条产出即触发关闭（CloseSpider 无 __str__，断言 reason）
        with pytest.raises(CloseSpider) as exc_info:
            s._bump_items()
        assert "3 条" in str(exc_info.value.reason)

    def test_bump_items_below_limit_ok(self):
        """上限内计数不抛异常。"""
        s = _make_spider()
        for _ in range(199):
            s._bump_items()
        assert s._items_collected == 199
