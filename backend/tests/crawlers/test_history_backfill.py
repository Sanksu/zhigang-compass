"""G-01 T2 历史回爬参数与 BOSS post_date 提取测试。

覆盖：
- boss_cdp_crawler：_extract_post_date 候选时间字段提取、_is_older_than_days
  截断判断、crawl(since_days=...) 翻页到旧岗位即停
- boss spider：history_days 参数透传为 --since-days 并放宽 --max-pages
- zhilian spider：history_days 放宽翻页上限 + 出现旧岗位停止翻页
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))

import pytest

from crawlers import boss_cdp_crawler as bcc
from crawlers.spiders import boss, zhilian

_CST = timezone(timedelta(hours=8))


def _now_ms() -> int:
    return int(datetime.now(_CST).timestamp() * 1000)


def _job(jid: str, ts_ms: int) -> dict:
    return {
        "encryptJobId": jid,
        "lastModifyTime": ts_ms,
        "jobName": f"岗位{jid}",
        "brandName": "公司",
        "skills": [],
        "jobLabels": [],
        "cityName": "北京",
        "salaryDesc": "20-30K",
        "jobExperience": "3-5年",
        "jobDegree": "本科",
    }


# ---------- boss_cdp_crawler._extract_post_date ----------

def test_extract_post_date_millis_timestamp():
    """毫秒时间戳 lastModifyTime 提取为东八区 ISO。"""
    job = _job("A1", _now_ms() - 10 * 86400 * 1000)
    post = bcc._extract_post_date(job)
    assert post.startswith("20")


def test_extract_post_date_iso_string_passthrough():
    """ISO 字符串时间字段原样返回。"""
    job = {"publishTime": "2026-08-01T10:00:00"}
    assert bcc._extract_post_date(job) == "2026-08-01T10:00:00"


def test_extract_post_date_first_nonempty_field_wins():
    """多个候选字段取第一个非空。"""
    job = {"lastModifyTime": "", "publishTime": _now_ms() - 86400 * 1000}
    post = bcc._extract_post_date(job)
    assert post.startswith("20")  # 走到第二个字段


def test_extract_post_date_empty_when_all_missing():
    """无任何时间字段时返回空串（外部数据缺失是合法状态）。"""
    assert bcc._extract_post_date({"encryptJobId": "X"}) == ""


def test_extract_post_date_invalid_value_skipped():
    """非法时间值跳过，落到后续候选字段。"""
    job = {"lastModifyTime": "abc", "publishTime": _now_ms()}
    post = bcc._extract_post_date(job)
    assert post.startswith("20")


# ---------- boss_cdp_crawler._is_older_than_days ----------

def test_is_older_than_days_true_for_100d_ago():
    old = (datetime.now(_CST) - timedelta(days=100)).isoformat()
    assert bcc._is_older_than_days(old, 90) is True


def test_is_older_than_days_false_for_10d_ago():
    fresh = (datetime.now(_CST) - timedelta(days=10)).isoformat()
    assert bcc._is_older_than_days(fresh, 90) is False


def test_is_older_than_days_unparsable_false():
    """无法解析的时间不断言（返回 False，不误截断）。"""
    assert bcc._is_older_than_days("not-a-date", 90) is False
    assert bcc._is_older_than_days("", 90) is False


# ---------- boss_cdp_crawler.crawl(since_days) 截断 ----------

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """按 page 返回预置 jobList，记录请求过的页码。"""

    def __init__(self, pages: dict[int, list]):
        self.pages = pages
        self.seen_pages: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        from urllib.parse import parse_qs, urlparse

        page = int(parse_qs(urlparse(url).query)["page"][0])
        self.seen_pages.append(page)
        return _FakeResp({"code": 0, "zpData": {"jobList": self.pages.get(page, [])}})


def _patch_crawl(monkeypatch, pages: dict[int, list]):
    async def fake_cookies(cdp_url, cookies_file=None):
        return mock.MagicMock()

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr(bcc, "read_zhipin_cookies", fake_cookies)
    client = _FakeClient(pages)
    monkeypatch.setattr(bcc.httpx, "Client", lambda **kw: client)
    monkeypatch.setattr(bcc.asyncio, "sleep", _no_sleep)
    return client


@pytest.mark.asyncio
async def test_crawl_since_days_stops_at_old_page(monkeypatch):
    """since_days=90 时翻到含旧岗位的页即停，且旧岗位不入库。"""
    now = _now_ms()
    pages = {
        1: [_job("P1", now - 3 * 86400 * 1000), _job("P2", now - 5 * 86400 * 1000)],
        2: [_job("OLD", now - 100 * 86400 * 1000), _job("P3", now - 10 * 86400 * 1000)],
        3: [_job("P4", now - 1 * 86400 * 1000)],  # 不应被请求
    }
    client = _patch_crawl(monkeypatch, pages)

    items = await bcc.crawl("http://127.0.0.1:9222", "Python", "101010100",
                            max_pages=5, since_days=90)

    assert client.seen_pages == [1, 2]  # 第 3 页未被请求
    assert len(items) == 3  # P1+P2+P3，旧岗位 OLD 不入库
    assert all(i["post_date"] for i in items)  # post_date 已提取
    assert {i["source_id"] for i in items} == {"P1", "P2", "P3"}


@pytest.mark.asyncio
async def test_crawl_without_since_days_pages_through(monkeypatch):
    """不带 since_days 时行为不变：照常翻页、不截断。"""
    now = _now_ms()
    pages = {
        1: [_job("P1", now - 3 * 86400 * 1000)],
        2: [_job("P2", now - 5 * 86400 * 1000)],
        3: [_job("P3", now - 100 * 86400 * 1000)],  # 旧岗位但仍请求
    }
    client = _patch_crawl(monkeypatch, pages)

    items = await bcc.crawl("http://127.0.0.1:9222", "Python", "101010100",
                            max_pages=3, since_days=None)

    assert client.seen_pages == [1, 2, 3]
    assert len(items) == 3


# ---------- boss spider：history_days 参数 ----------

def test_boss_spider_default_cmd_no_since_days():
    spider = boss.BossSpider()
    cmd = spider._build_cmd({
        "keyword": "Python", "city": "北京", "city_code": "101010100",
    })
    assert "--max-pages" in cmd
    assert cmd[cmd.index("--max-pages") + 1] == "5"
    assert "--since-days" not in cmd


def test_boss_spider_history_days_passes_since_days_and_wider_pages():
    spider = boss.BossSpider(history_days="90")
    cmd = spider._build_cmd({
        "keyword": "Python", "city": "北京", "city_code": "101010100",
    })
    assert "--since-days" in cmd
    assert cmd[cmd.index("--since-days") + 1] == "90"
    # 放宽翻页上限，让时间截断有机会提前生效
    assert int(cmd[cmd.index("--max-pages") + 1]) > 5


def test_boss_spider_passes_post_date_into_item(monkeypatch):
    """parse 产出的 JobItem 透传 post_date（BOSS post_date 补提取落库）。"""
    spider = boss.BossSpider()
    line = {
        "source_id": "E1", "source_url": "https://x", "title": "T",
        "company": "C", "location": "L", "salary": "S", "experience": "E",
        "education": "D", "tags": [], "description": "", "requirements": "",
        "raw_text": "R", "post_date": "2026-07-01T10:00:00+08:00",
    }
    item = spider.make_item(
        source_id=line["source_id"], source_url=line["source_url"],
        title=line["title"], company=line["company"], location=line["location"],
        salary=line["salary"], experience=line["experience"],
        education=line["education"], tags=line["tags"],
        description=line["description"], requirements=line["requirements"],
        raw_text=line["raw_text"], post_date=line["post_date"],
    )
    assert item["post_date"] == "2026-07-01T10:00:00+08:00"


# ---------- zhilian spider：history_days ----------

def test_zhilian_default_max_pages_5():
    spider = zhilian.ZhilianSpider()
    assert spider._max_pages == 5


def test_zhilian_history_days_widens_max_pages():
    spider = zhilian.ZhilianSpider(history_days="90")
    assert spider._max_pages > 5


def test_zhilian_cutoff_reached_only_when_history_days():
    spider = zhilian.ZhilianSpider()
    assert spider._cutoff_reached({}) is False

    hd = zhilian.ZhilianSpider(history_days="90")
    old = (datetime.now(_CST) - timedelta(days=100)).strftime("%Y-%m-%d")
    fresh = datetime.now(_CST).strftime("%Y-%m-%d")
    assert hd._cutoff_reached({"1": old}) is True
    assert hd._cutoff_reached({"2": fresh}) is False


# ---------- jobspy 源（indeed/linkedin）：history_days → --days-old ----------

def _indeed_spider(**kwargs):
    from crawlers.spiders import indeed
    return indeed.IndeedSpider(**kwargs)


def test_jobspy_build_cmd_default_no_days_old():
    cmd = _indeed_spider()._build_cmd("Python", "New York")
    assert "--days-old" not in cmd
    assert "--site" in cmd and "indeed" in cmd
    assert "--results-wanted" in cmd


def test_jobspy_build_cmd_passes_days_old():
    cmd = _indeed_spider(history_days="90")._build_cmd("Python", "New York")
    assert "--days-old" in cmd
    assert cmd[cmd.index("--days-old") + 1] == "90"


def test_jobspy_crawler_crawl_uses_days_old_hours(monkeypatch):
    """jobspy_crawler.crawl 把 days_old 换算为 hours_old 传给 scrape_jobs。"""
    import pandas as pd

    from crawlers import jobspy_crawler as jc

    captured = {}

    def _fake_scrape_jobs(**kw):
        captured.update(kw)
        return pd.DataFrame([])

    # jobspy 在 crawl 内部导入，通过 sys.modules 注入 fake 模块
    fake_jobspy = type(sys.modules[__name__])("jobspy", ())
    fake_jobspy.scrape_jobs = _fake_scrape_jobs
    monkeypatch.setitem(sys.modules, "jobspy", fake_jobspy)

    jc.crawl(site="indeed", keyword="Python", city="New York",
             results_wanted=5, days_old=90)

    assert captured["hours_old"] == 90 * 24
    assert captured["site_name"] == ["indeed"]
    assert captured["results_wanted"] == 5


# ---------- mock 90 天前数据：验证旧岗位 date_posted 在链路中保留 ----------

def test_jobspy_crawl_keeps_90day_old_post_date(monkeypatch, capsys):
    """mock scrape_jobs 返回 90 天前发布的岗位：crawl 输出 JSONL 保留 date_posted。"""
    import json as _json

    import pandas as pd

    from crawlers import jobspy_crawler as jc

    old_date = (datetime.now(_CST) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")
    df = pd.DataFrame([{
        "id": "job-old-90", "title": "Old Engineer", "company": "ACME",
        "city": "New York", "state": "NY", "location": "New York, NY",
        "date_posted": old_date, "job_url": "https://indeed.com/viewjob?jk=old90",
        "description": "legacy post", "min_amount": "", "max_amount": "",
        "salary_interval": "", "currency": "USD", "job_type": "fulltime",
        "is_remote": False, "job_level": "", "skills": "", "experience_range": "",
    }])

    fake_jobspy = type(sys.modules[__name__])("jobspy", ())
    fake_jobspy.scrape_jobs = lambda **kw: df
    monkeypatch.setitem(sys.modules, "jobspy", fake_jobspy)

    rc = jc.crawl(site="indeed", keyword="Python", city="New York",
                  results_wanted=5, days_old=90)
    out = capsys.readouterr().out
    lines = [_json.loads(l) for l in out.strip().splitlines()]

    assert rc == 0
    assert len(lines) == 1
    assert lines[0]["id"] == "job-old-90"
    assert lines[0]["date_posted"] == old_date  # 90 天前数据未被过滤/丢弃


def test_jobspy_spider_passes_old_post_date_into_item():
    """indeed spider 从 JSONL 构造 item 时透传 90 天前 post_date（与 boss 一致）。"""
    old_date = (datetime.now(_CST) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")
    spider = _indeed_spider()
    item = spider.make_item(
        source_id="job-old-90",
        source_url="https://indeed.com/viewjob?jk=old90",
        title="Old Engineer",
        company="ACME",
        location="New York, NY",
        salary="",
        experience="",
        education="",
        tags=[],
        description="legacy post",
        requirements="",
        raw_text="{}",
        post_date=old_date,
    )
    assert item["post_date"] == old_date


# ---------- glassdoor（CDP 源）：history_days 放宽翻页上限 ----------

def _glassdoor_spider(**kwargs):
    from crawlers.spiders import glassdoor
    return glassdoor.GlassdoorSpider(**kwargs)


def test_glassdoor_default_max_pages_2():
    assert _glassdoor_spider().max_pages == 2


def test_glassdoor_history_days_widens_max_pages():
    assert _glassdoor_spider(history_days="90").max_pages > 2


def test_glassdoor_explicit_max_pages_wins():
    """显式 -a max_pages 优先于 history_days 放宽。"""
    assert _glassdoor_spider(history_days="90", max_pages="3").max_pages == 3
