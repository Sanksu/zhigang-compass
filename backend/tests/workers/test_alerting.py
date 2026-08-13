"""webhook 告警服务与接入点测试（设计文档 §4.4 异常阈值告警 / §11.1 配置）。

覆盖：
- send_alert 本体：未配置跳过、配置后 POST、发送失败不抛异常
- crawl_platform 失败 → crawl_failed 告警
- check_data_freshness 有过期来源 → data_stale 告警
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.services import alerting
from app.workers import tasks


class TestSendAlert:
    def test_unconfigured_skips_without_sending(self):
        with mock.patch.object(alerting.settings, "alert_webhook_url", ""):
            ok = asyncio.run(alerting.send_alert("crawl_failed", "某爬虫失败"))
        assert ok is False

    def test_configured_posts_json_payload(self):
        captured = {}

        def _fake_urlopen(req, timeout=5):
            captured["url"] = req.full_url
            captured["data"] = json.loads(req.data.decode("utf-8"))
            return mock.MagicMock()

        with mock.patch.object(alerting.settings, "alert_webhook_url", "https://example.com/hook"):
            with mock.patch.object(alerting.urllib.request, "urlopen", _fake_urlopen):
                ok = asyncio.run(alerting.send_alert("data_stale", "数据过期", stale=["jd:boss"]))

        assert ok is True
        assert captured["url"] == "https://example.com/hook"
        assert captured["data"]["event"] == "data_stale"
        assert captured["data"]["stale"] == ["jd:boss"]

    def test_send_failure_returns_false(self):
        def _boom(*args, **kwargs):
            raise OSError("network down")

        with mock.patch.object(alerting.settings, "alert_webhook_url", "https://example.com/hook"):
            with mock.patch.object(alerting.urllib.request, "urlopen", _boom):
                ok = asyncio.run(alerting.send_alert("crawl_failed", "msg"))
        assert ok is False


class _FakeStream:
    async def readline(self):
        return b""


class _FakeFailedProc:
    returncode = 1
    stdout = _FakeStream()
    stderr = _FakeStream()

    async def wait(self):
        return 1


class TestCrawlFailureAlert:
    def test_nonzero_exit_triggers_alert(self, monkeypatch, tmp_path):
        """退出码非 0 时除抛 RuntimeError 外，还需发送 crawl_failed 告警。"""
        calls = []

        async def _fake_alert(event, message, **extra):
            calls.append((event, message, extra))

        async def _noop_log(ctx, task_id, line):
            return None

        async def _fake_update(task_id, **fields):
            return None

        class _FakeAsyncio:
            async def create_subprocess_exec(self, *args, **kwargs):
                return _FakeFailedProc()

            async def gather(self, *aws):
                await asyncio.gather(*aws)

            async def wait_for(self, aw, timeout):
                return await aw

            TimeoutError = asyncio.TimeoutError

        monkeypatch.setattr(tasks, "send_alert", _fake_alert)
        monkeypatch.setattr(tasks, "asyncio", _FakeAsyncio())
        monkeypatch.setattr(tasks, "_OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(tasks, "_CRAWLERS_DIR", tmp_path.parent.parent)
        monkeypatch.setattr(tasks, "_push_crawl_log", _noop_log)
        monkeypatch.setattr(tasks, "_update_crawl_task", _fake_update)

        async def run():
            try:
                await tasks.crawl_platform({}, "boss", task_id="t-alert")
            except RuntimeError:
                pass

        asyncio.run(run())

        assert calls and calls[0][0] == "crawl_failed"
        assert "boss" in calls[0][1]
        assert calls[0][2]["spider"] == "boss"


class TestStaleDataAlert:
    class _FakeScalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return list(self._rows)

    def test_stale_sources_trigger_alert(self, monkeypatch):
        """check_data_freshness 存在过期来源时发送 data_stale 告警。"""
        calls = []

        async def _fake_alert(event, message, **extra):
            calls.append((event, message, extra))

        stale_at = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=3)).isoformat()

        class _FakeSession:
            def __init__(self):
                self.rows = [
                    mock.Mock(source="boss", crawled_at=stale_at),
                    mock.Mock(source="zhilian", crawled_at=stale_at),
                ]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def scalars(self, stmt):
                return TestStaleDataAlert._FakeScalars(self.rows)

        def _factory():
            return _FakeSession()

        monkeypatch.setattr(tasks, "send_alert", _fake_alert)
        monkeypatch.setattr("app.core.database.async_session_factory", _factory)

        result = asyncio.run(tasks.check_data_freshness({}))

        assert result["stale_sources"], "应有过期来源"
        assert calls and calls[0][0] == "data_stale"
        assert "boss" in calls[0][1]

    def test_fresh_data_no_alert(self, monkeypatch):
        """全部来源新鲜（≤ T+1）时不发送告警。"""
        calls = []

        async def _fake_alert(event, message, **extra):
            calls.append((event, message, extra))

        fresh_at = datetime.now(timezone(timedelta(hours=8))).isoformat()

        class _FakeSession:
            def __init__(self):
                self.rows = [mock.Mock(source="boss", crawled_at=fresh_at)]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def scalars(self, stmt):
                return TestStaleDataAlert._FakeScalars(self.rows)

        def _factory():
            return _FakeSession()

        monkeypatch.setattr(tasks, "send_alert", _fake_alert)
        monkeypatch.setattr("app.core.database.async_session_factory", _factory)

        asyncio.run(tasks.check_data_freshness({}))

        assert calls == []
