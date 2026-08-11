"""crawl_platform 产出判定测试（修复"爬取 0 条仍显示成功"）。

回归背景：crawl_platform 此前仅按退出码判定，爬虫退出码 0 但产出 0 条
（关键词无结果 / 反爬静默拦截）仍写 status="success"。
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.workers import tasks


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

    async def _noop_log(ctx, task_id, line):
        return None

    async def _fake_update(task_id, **fields):
        updates.append((task_id, fields))

    monkeypatch.setattr(tasks, "asyncio", _FakeAsyncio())
    monkeypatch.setattr(tasks, "datetime", _FrozenDateTime)
    monkeypatch.setattr(tasks, "_OUTPUT_DIR", tmp_path)
    # _CRAWLERS_DIR 用于 output 相对路径（relative_to），指向 tmp_path 的祖父目录
    monkeypatch.setattr(tasks, "_CRAWLERS_DIR", tmp_path.parent.parent)
    monkeypatch.setattr(tasks, "_push_crawl_log", _noop_log)
    monkeypatch.setattr(tasks, "_update_crawl_task", _fake_update)
    return updates, cmd_calls


def test_zero_items_marks_failed(monkeypatch, tmp_path):
    """退出码 0 但 output 文件为空 → 标记 failed 并抛 RuntimeError。"""
    updates, _ = _patch_env(monkeypatch, tmp_path)
    (tmp_path / "boss_20260803_120001.jsonl").write_text("", encoding="utf-8")

    async def run():
        with pytest.raises(RuntimeError, match="产出 0 条数据"):
            await tasks.crawl_platform({}, "boss", task_id="t-zero")

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
        return await tasks.crawl_platform({}, "boss", task_id="t-ok")

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
        return await tasks.crawl_platform(
            {}, "indeed", keywords=["Python"], cities=["New York"], task_id="t-city"
        )

    result = asyncio.run(run())
    assert result["items"] == 1
    assert cmd_calls, "应至少发起一次 scrapy 子进程"
    cmd = cmd_calls[0]
    assert "-a" in cmd
    assert f"cities=New York" in cmd
    assert f"keywords=Python" in cmd
