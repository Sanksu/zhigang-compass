"""zhilian 页面级空列表退避重试单元测试（08-21c 反爬加固）。

覆盖：
- _max_empty_retries 配置读取（默认 3 / 显式 0=关闭 / 显式值）
- parse 空列表时按 max_empty_retries 触发 reactor 退避重发，计数递增
- 用尽重试额度后不再调度，走原"跳过"逻辑
- max_empty_retries=0 关闭时不再调度
"""

import sys
from pathlib import Path

import pytest
from scrapy.http import Request, TextResponse

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))

import app.core.runtime_config as runtime_config
from crawlers.spiders import zhilian as zhilian_mod
from crawlers.spiders.zhilian import ZhilianSpider


def _fake_response(url="https://sou.zhaopin.com/?kw=Python&pn=1", page=1):
    req = Request(url=url, meta={"keyword": "Python", "city": "北京", "page": page})
    return TextResponse(
        url=url,
        body=b"<html><body><div class='joblist'></div></body></html>",
        request=req,
        encoding="utf-8",
    )


class _FakeEngine:
    def __init__(self):
        self.scheduled = []

    def schedule(self, request, spider):
        self.scheduled.append(request)


def _make_spider(**kwargs):
    """构造不启动网络的爬虫实例，并挂载假 crawler/engine 供空列表重试调度。"""
    spider = ZhilianSpider.__new__(ZhilianSpider)
    spider.name = "zhilian"
    spider.platform = "zhilian"
    spider.keywords = ["Python"]
    spider.cities = ["北京"]
    spider.history_days = 0
    spider._max_pages = 5
    spider._max_results = 200
    spider._items_collected = 0
    spider._max_empty_retries = kwargs.get("max_empty_retries", 3)
    spider._empty_retries_used = 0
    spider.crawler = kwargs.get("crawler", type("C", (), {"engine": _FakeEngine()})())
    return spider


def _patch_get(monkeypatch, data: dict | None):
    """覆盖 runtime_config.get，按 key 返回，未命中返回默认。"""
    monkeypatch.setattr(runtime_config, "get", lambda key, default=None: data.get(key, {}) if data else {})


def _patch_logger(spider, monkeypatch):
    # Scrapy 的 logger 是 property 无 setter；用类级替换屏蔽。
    class Log:
        def warning(self, *a, **k):
            return None

        def info(self, *a, **k):
            return None

    monkeypatch.setattr(ZhilianSpider, "logger", Log())


# ---- _max_empty_retries 配置读取 ----
def test_max_empty_retries_default(monkeypatch):
    """未配置时默认 3 次。"""
    _patch_get(monkeypatch, None)
    assert zhilian_mod._max_empty_retries() == 3


def test_max_empty_retries_explicit_zero(monkeypatch):
    """配置 max_empty_retries=0 表示关闭重试。"""
    _patch_get(monkeypatch, {"crawlers": {"zhilian": {"max_empty_retries": 0}}})
    assert zhilian_mod._max_empty_retries() == 0


def test_max_empty_retries_explicit_value(monkeypatch):
    """显式配置生效。"""
    _patch_get(monkeypatch, {"crawlers": {"zhilian": {"max_empty_retries": 5}}})
    assert zhilian_mod._max_empty_retries() == 5


# ---- parse 空列表退避重试 ----
@pytest.fixture
def _scheduled_calls(monkeypatch):
    """只为实际重试测试记录 _call_later 的完整调用参数。"""
    calls = []

    class Nameable:
        pass

    def _fake_request(url, meta=None, **kwargs):
        request = Nameable()
        request.url = url
        request.meta = dict(meta or {})
        return request

    def _record_call_later(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(zhilian_mod, "make_playwright_request", _fake_request)
    monkeypatch.setattr(zhilian_mod, "_call_later", _record_call_later)
    return calls


def test_empty_list_schedules_retry_and_increments(monkeypatch, _scheduled_calls):
    """空列表通过惰性 _call_later 调度重发，且计数递增。

    第八轮 P2-21：延迟回调改为包装函数（回调体内捕获 engine.schedule 异常，
    防 spider 关闭后 unhandled error 裸抛进 reactor）——断言改为触发回调后
    检查 engine 收到重试请求。
    """
    spider = _make_spider()
    _patch_logger(spider, monkeypatch)
    response = _fake_response()

    result = list(spider.parse(response))
    assert result == []  # parse 空列表无 Item 产出
    assert spider._empty_retries_used == 1  # 计数递增
    assert len(_scheduled_calls) == 1

    args, kwargs = _scheduled_calls[0]
    delay, callback = args
    assert kwargs == {}
    assert delay == zhilian_mod.backoff_delay(0)  # 首次退避 30s
    callback()  # 退避窗口结束：请求经 engine.schedule 重入调度
    (req,) = spider.crawler.engine.scheduled
    assert req.url == response.url  # 重发同一搜索 URL
    assert req.meta == {"keyword": "Python", "city": "北京", "page": 1}


def test_delayed_retry_after_spider_close_not_raises(monkeypatch, _scheduled_calls):
    """第八轮 P2-21：spider 关闭后延迟回调中的调度异常被捕获，不向 reactor 裸抛。"""
    spider = _make_spider()
    _patch_logger(spider, monkeypatch)
    list(spider.parse(_fake_response()))
    assert len(_scheduled_calls) == 1
    (_, callback), _ = _scheduled_calls[0]

    def _boom(request, spider_arg):
        raise RuntimeError("engine not running")

    spider.crawler.engine.schedule = _boom
    callback()  # 不抛异常即通过（warning 已由假 logger 吸收）


def test_empty_list_scheduler_exception_propagates(monkeypatch):
    """惰性调度器失败时不吞异常，交由 Scrapy 正常处理。"""
    spider = _make_spider()
    _patch_logger(spider, monkeypatch)

    def _raise_scheduler_error(*args, **kwargs):
        raise RuntimeError("scheduler unavailable")

    monkeypatch.setattr(zhilian_mod, "_call_later", _raise_scheduler_error)

    with pytest.raises(RuntimeError, match="scheduler unavailable"):
        list(spider.parse(_fake_response()))


def test_empty_list_exhausts_no_schedule(monkeypatch):
    """用尽 max_empty_retries 后不再调度，走原跳过逻辑。"""
    spider = _make_spider(max_empty_retries=1)
    spider._empty_retries_used = 1  # 已用尽
    _patch_logger(spider, monkeypatch)

    result = list(spider.parse(_fake_response()))
    assert result == []
    assert spider._empty_retries_used == 1  # 不再递增


def test_empty_list_disabled_no_schedule(monkeypatch):
    """max_empty_retries=0 关闭重试。"""
    spider = _make_spider(max_empty_retries=0)
    _patch_logger(spider, monkeypatch)

    result = list(spider.parse(_fake_response()))
    assert result == []