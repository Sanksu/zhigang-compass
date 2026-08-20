"""BackoffRetryMiddleware 指数退避测试。

覆盖：退避序列计算（30→60→120→300 封顶）、429/403 拦截延迟重试、
退避次数用尽置 dont_retry、非 429/403 不拦截。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))

import crawlers.middlewares as mw
from crawlers.middlewares import BackoffRetryMiddleware, backoff_delay, retry_after_seconds


# ── 退避序列 ──


def test_backoff_sequence():
    # 设计文档 §4：30s → 60s → 120s → 300s（封顶）
    assert backoff_delay(0) == 30
    assert backoff_delay(1) == 60
    assert backoff_delay(2) == 120
    assert backoff_delay(3) == 300
    assert backoff_delay(4) == 300


# ── 中间件行为 ──


class _FakeRequest:
    def __init__(self, meta=None, url="https://example.com/jobs"):
        self.meta = dict(meta or {})
        self.url = url
        self.dont_filter = False
        self.scheduled = 0

    def replace(self, **kw):
        r = _FakeRequest(meta=self.meta, url=self.url)
        r.dont_filter = kw.get("dont_filter", self.dont_filter)
        return r


class _FakeResponse:
    def __init__(self, status, headers=None):
        self.status = status
        self.headers = headers or {}


class _FakeSpider:
    name = "test"

    def __init__(self, retry_times=3):
        self.settings = type("S", (), {"getint": lambda self, k, d: retry_times})()
        self.logs = []
        self.logger = type(
            "L",
            (),
            {
                "error": lambda self, msg: self,
                "warning": lambda self, msg: self,
            },
        )()


class _FakeCrawler:
    def __init__(self):
        self.engine = type(
            "E",
            (),
            {"schedule": lambda self, req, spider: req.__setattr__("scheduled", 1)},
        )()


def _make_mw(monkeypatch):
    """构造中间件并接管 reactor.callLater（记录延迟与回调，不真等待）。"""
    crawler = _FakeCrawler()
    mw_inst = BackoffRetryMiddleware(crawler)
    calls = []

    class _FakeReactor:
        @staticmethod
        def callLater(delay, func, *args):
            calls.append((delay, func, args))

    monkeypatch.setattr(mw, "reactor", _FakeReactor())
    return mw_inst, crawler, calls


def test_passes_non_429_403(monkeypatch):
    mw_inst, _, calls = _make_mw(monkeypatch)
    spider = _FakeSpider()
    resp = _FakeResponse(200)
    out = mw_inst.process_response(_FakeRequest(), resp, spider)
    assert out is resp
    assert calls == []  # 未触发退避调度


def test_backoff_schedules_delayed_retry(monkeypatch):
    mw_inst, crawler, calls = _make_mw(monkeypatch)
    spider = _FakeSpider()
    req = _FakeRequest()
    out = mw_inst.process_response(req, _FakeResponse(429), spider)
    assert out is None  # 请求已由延迟调度接管
    assert len(calls) == 1
    delay, func, args = calls[0]
    assert delay == 30  # 首次退避
    assert func == crawler.engine.schedule
    retry_req, s = args
    assert s is spider
    assert retry_req is not req
    assert retry_req.dont_filter is True
    assert retry_req.meta["backoff_count"] == 1


def test_backoff_grows_exponentially(monkeypatch):
    mw_inst, crawler, calls = _make_mw(monkeypatch)
    spider = _FakeSpider()
    req = _FakeRequest(meta={"backoff_count": 2})
    mw_inst.process_response(req, _FakeResponse(403), spider)
    assert calls[0][0] == 120


def test_exhausts_sets_dont_retry(monkeypatch):
    mw_inst, _, calls = _make_mw(monkeypatch)
    spider = _FakeSpider(retry_times=3)
    req = _FakeRequest(meta={"backoff_count": 3})
    resp = _FakeResponse(429)
    out = mw_inst.process_response(req, resp, spider)
    assert out is resp  # 交还响应，让内置 RetryMiddleware 收尾
    assert calls == []  # 不再延迟调度
    assert req.meta["dont_retry"] is True  # 阻止内置中间件即时重试


def test_403_also_backoffs(monkeypatch):
    mw_inst, _, calls = _make_mw(monkeypatch)
    spider = _FakeSpider()
    mw_inst.process_response(_FakeRequest(), _FakeResponse(403), spider)
    assert calls[0][0] == 30


def test_retry_after_seconds_parser():
    """Retry-After 头解析：秒数 / 缺失 / 非法值。"""
    assert retry_after_seconds(_FakeResponse(429, headers={"Retry-After": b"45"})) == 45
    assert retry_after_seconds(_FakeResponse(429)) is None
    assert retry_after_seconds(_FakeResponse(429, headers={"Retry-After": b"abc"})) is None
    assert retry_after_seconds(_FakeResponse(429, headers={"Other": b"60"})) is None


def test_honors_retry_after(monkeypatch):
    """403/429 带 Retry-After 时按其建议退避（而非固定指数 30s）。"""
    mw_inst, crawler, calls = _make_mw(monkeypatch)
    spider = _FakeSpider()
    resp = _FakeResponse(429, headers={"Retry-After": b"60"})
    out = mw_inst.process_response(_FakeRequest(), resp, spider)
    assert out is None
    assert len(calls) == 1
    assert calls[0][0] == 60
    assert calls[0][1] == crawler.engine.schedule


def test_retry_after_capped(monkeypatch):
    """Retry-After 超长时封顶到退避上限（300s），防服务端异常建议拖住任务。"""
    mw_inst, _, calls = _make_mw(monkeypatch)
    spider = _FakeSpider()
    resp = _FakeResponse(403, headers={"Retry-After": b"99999"})
    mw_inst.process_response(_FakeRequest(), resp, spider)
    assert calls[0][0] == 300


def test_missing_retry_after_falls_back_to_backoff(monkeypatch):
    """无 Retry-After 头时维持既有指数退避（30s 起步）。"""
    mw_inst, _, calls = _make_mw(monkeypatch)
    spider = _FakeSpider()
    out = mw_inst.process_response(_FakeRequest(), _FakeResponse(429), spider)
    assert out is None
    assert calls[0][0] == 30
