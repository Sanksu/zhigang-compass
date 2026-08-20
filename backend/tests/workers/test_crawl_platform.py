"""crawl_platform 产出判定测试（修复"爬取 0 条仍显示成功"）。

回归背景：crawl_platform 此前仅按退出码判定，爬虫退出码 0 但产出 0 条
（关键词无结果 / 反爬静默拦截）仍写 status="success"。
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.workers import crawl


class _FakeStream:
    async def readline(self):
        return b""


class _FakeProc:
    returncode = 0
    stdout = _FakeStream()
    stderr = _FakeStream()

    async def wait(self):
        return 0


class _FrozenDateTime:
    """固定 CST 时间，让 crawl_platform 生成的 output 文件名可预测。"""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 8, 3, 12, 0, 1, tzinfo=timezone(timedelta(hours=8)))


def _patch_env(monkeypatch, tmp_path) -> list[tuple[str, dict]]:
    """替换子进程创建/状态更新/日志推送/时间，output 目录指向 tmp_path。"""
    updates: list[tuple[str, dict]] = []
    cmd_calls: list[list[str]] = []

    class _FakeAsyncio:
        async def create_subprocess_exec(self, *args, **kwargs):
            cmd_calls.append(list(args))
            return _FakeProc()

        async def gather(self, *aws):
            await asyncio.gather(*aws)

        async def wait_for(self, aw, timeout):
            # 透传：FakeProc.wait 立即返回，测试不触发超时分支
            return await aw

        TimeoutError = asyncio.TimeoutError

    async def _noop_log(ctx, task_id, line):
        return None

    async def _fake_update(task_id, **fields):
        updates.append((task_id, fields))

    monkeypatch.setattr(crawl, "asyncio", _FakeAsyncio())
    monkeypatch.setattr(crawl, "datetime", _FrozenDateTime)
    monkeypatch.setattr(crawl, "_OUTPUT_DIR", tmp_path)
    # _CRAWLERS_DIR 用于 output 相对路径（relative_to），指向 tmp_path 的祖父目录
    monkeypatch.setattr(crawl, "_CRAWLERS_DIR", tmp_path.parent.parent)
    monkeypatch.setattr(crawl, "push_crawl_log", _noop_log)
    monkeypatch.setattr(crawl, "update_crawl_task", _fake_update)
    return updates, cmd_calls


def test_zero_items_marks_failed(monkeypatch, tmp_path):
    """退出码 0 但 output 文件为空 → 标记 failed 并抛 RuntimeError。"""
    updates, _ = _patch_env(monkeypatch, tmp_path)
    (tmp_path / "boss_20260803_120001.jsonl").write_text("", encoding="utf-8")

    async def run():
        with pytest.raises(RuntimeError, match="产出 0 条数据"):
            await crawl.crawl_platform({}, "boss", task_id="t-zero")

    asyncio.run(run())
    assert updates[-1][0] == "t-zero"
    assert updates[-1][1]["status"] == "failed"
    assert "产出 0 条数据" in updates[-1][1]["error"]


def test_nonzero_items_marks_success(monkeypatch, tmp_path):
    """退出码 0 且有产出 → 标记 success 并返回 items 计数。"""
    updates, _ = _patch_env(monkeypatch, tmp_path)
    (tmp_path / "boss_20260803_120001.jsonl").write_text(
        "{}\n{}\n", encoding="utf-8"
    )

    async def run():
        return await crawl.crawl_platform({}, "boss", task_id="t-ok")

    result = asyncio.run(run())
    assert result["items"] == 2
    assert updates[-1][0] == "t-ok"
    assert updates[-1][1]["status"] == "success"
    assert updates[-1][1]["result"]["items"] == 2


def test_cities_passed_to_scrapy_cmd(monkeypatch, tmp_path):
    """cities 参数透传为 scrapy -a cities=...（前端 city → spider 城市过滤）。"""
    _, cmd_calls = _patch_env(monkeypatch, tmp_path)
    (tmp_path / "indeed_20260803_120001.jsonl").write_text("{}\n", encoding="utf-8")

    async def run():
        return await crawl.crawl_platform(
            {}, "indeed", keywords=["Python"], cities=["New York"], task_id="t-city"
        )

    result = asyncio.run(run())
    assert result["items"] == 1
    assert cmd_calls, "应至少发起一次 scrapy 子进程"
    cmd = cmd_calls[0]
    assert "-a" in cmd
    assert "cities=New York" in cmd
    assert "keywords=Python" in cmd


def test_timeout_kills_subprocess_and_marks_failed(monkeypatch, tmp_path):
    """爬虫超过 _CRAWL_TIMEOUT_SEC 未退出 → kill + 标记 failed + 抛 RuntimeError。"""
    killed = []

    class _SlowProc(_FakeProc):
        async def wait(self):
            return 0

        def kill(self):
            killed.append(True)

    class _SlowAsyncio:
        async def create_subprocess_exec(self, *args, **kwargs):
            return _SlowProc()

        def gather(self, *aws):
            # 超时路径：gather 不会被真正调度（wait_for 直接抛超时），
            # 参数协程（_drain 等）在创建后从未 await，close 防 RuntimeWarning
            for aw in aws:
                getattr(aw, "close", lambda: None)()
            return asyncio.sleep(0)

        async def wait_for(self, aw, timeout):
            getattr(aw, "close", lambda: None)()
            raise asyncio.TimeoutError()

        TimeoutError = asyncio.TimeoutError

    updates = []
    async def _noop_log(ctx, task_id, line):
        return None

    async def _fake_update(task_id, **fields):
        updates.append((task_id, fields))

    async def _fake_alert(kind, msg, **kwargs):
        alerts.append(kind)

    alerts = []
    monkeypatch.setattr(crawl, "asyncio", _SlowAsyncio())
    monkeypatch.setattr(crawl, "datetime", _FrozenDateTime)
    monkeypatch.setattr(crawl, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(crawl, "_CRAWLERS_DIR", tmp_path.parent.parent)
    monkeypatch.setattr(crawl, "push_crawl_log", _noop_log)
    monkeypatch.setattr(crawl, "update_crawl_task", _fake_update)
    monkeypatch.setattr(crawl, "send_alert", _fake_alert)

    async def run():
        with pytest.raises(RuntimeError, match="超时"):
            await crawl.crawl_platform({}, "zhilian", task_id="t-timeout")

    asyncio.run(run())
    assert killed, "超时必须 kill 子进程"
    assert updates[-1][0] == "t-timeout"
    assert updates[-1][1]["status"] == "failed"
    assert alerts == ["crawl_timeout"]


# ============================================================
# crawl_scheduler（08-21b 每爬虫独立触发时间）
# ============================================================


def _patch_scheduler(monkeypatch, crawlers_cfg: dict, now=None):
    """替换 runtime_config / crawl_platform / 锁，固定当前时间。"""
    triggered: list[str] = []

    async def _fake_crawl_platform(ctx, spider, **kwargs):
        triggered.append(spider)
        return {"spider": spider, "items": 1}

    async def _fake_lock(spider, run_date):
        return True  # 默认放行

    if now is None:
        now = datetime(2026, 8, 3, 7, 30, tzinfo=timezone(timedelta(hours=8)))

    class _FrozenNow:
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(crawl, "runtime_config", type("RC", (), {"get": staticmethod(lambda k, d=None: crawlers_cfg if k == "crawlers" else d)})())
    monkeypatch.setattr(crawl, "crawl_platform", _fake_crawl_platform)
    monkeypatch.setattr(crawl, "_crawl_run_lock_acquire", _fake_lock)
    monkeypatch.setattr(crawl, "datetime", _FrozenNow)
    return triggered


def test_crawl_scheduler_triggers_matching_spider(monkeypatch):
    """当前 HH:MM 匹配配置的爬虫被触发。"""
    triggered = _patch_scheduler(monkeypatch, {
        "zhilian": {"hour": 7, "minute": 30},
        "arxiv": {"hour": 6, "minute": 0},
    }, now=datetime(2026, 8, 3, 7, 30, tzinfo=timezone(timedelta(hours=8))))
    result = asyncio.run(crawl.crawl_scheduler({}))
    assert triggered == ["zhilian"]
    assert result["run_date"] == "2026-08-03"
    assert result["triggered"][0]["spider"] == "zhilian"


def test_crawl_scheduler_skips_nonmatching(monkeypatch):
    """未到点/未配置时间的爬虫均不触发。"""
    triggered = _patch_scheduler(monkeypatch, {
        "zhilian": {"hour": 7, "minute": 30},
        "github": {"enabled": True},  # 无独立时间 → 跳过（并入主管线）
    }, now=datetime(2026, 8, 3, 8, 0, tzinfo=timezone(timedelta(hours=8))))
    result = asyncio.run(crawl.crawl_scheduler({}))
    assert triggered == []  # 8:00 不匹配 7:30；github 无 hour/minute


def test_crawl_scheduler_skips_disabled(monkeypatch):
    """enabled=false 且时间匹配也跳过。"""
    triggered = _patch_scheduler(monkeypatch, {
        "zhilian": {"hour": 7, "minute": 30, "enabled": False},
    }, now=datetime(2026, 8, 3, 7, 30, tzinfo=timezone(timedelta(hours=8))))
    asyncio.run(crawl.crawl_scheduler({}))
    assert triggered == []


def test_crawl_scheduler_respects_day_lock(monkeypatch):
    """当日幂等锁命中 → 跳过（防重触发）。"""
    triggered = _patch_scheduler(monkeypatch, {
        "zhilian": {"hour": 7, "minute": 30},
    }, now=datetime(2026, 8, 3, 7, 30, tzinfo=timezone(timedelta(hours=8))))
    async def _locked(spider, run_date):
        return False  # 锁命中

    monkeypatch.setattr(crawl, "_crawl_run_lock_acquire", _locked)
    result = asyncio.run(crawl.crawl_scheduler({}))
    assert triggered == []
    assert result["triggered"][0]["skipped"] == "duplicate_day_lock"
