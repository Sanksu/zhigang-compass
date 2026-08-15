"""G-01 T3 历史回爬脚本 history_backfill.py 测试。

覆盖：
- build_cmd：scrapy 命令构造（platform、history_days、max_pages、-o）
- run_backfill：单源成功统计 / 单源失败退避重试 + 告警 / 多个平台串行
- 统计报告写入
"""

import asyncio
import sys
from pathlib import Path
from unittest import mock

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))

import pytest

from scripts import history_backfill as hb


# ---------- build_cmd ----------

def test_build_cmd_includes_days_and_output():
    cmd = hb.build_cmd("boss", days=90, max_pages=50, output_file=Path("/tmp/x.jsonl"))
    assert cmd[:4] == [sys.executable, "-m", "scrapy", "crawl"]
    assert "boss" in cmd
    assert "-a" in cmd and "history_days=90" in cmd
    assert "-a" in cmd and "max_pages=50" in cmd
    assert "-o" in cmd and str(Path("/tmp/x.jsonl")) in cmd


def test_build_cmd_no_days_omits_flag():
    cmd = hb.build_cmd("zhilian", days=0, max_pages=5, output_file=Path("x.jsonl"))
    assert "history_days" not in cmd
    assert "max_pages=5" in cmd


def test_default_platforms_cover_domestic_and_international():
    """回爬默认覆盖国内 A 级 + 国际源；linkedin/monster 不在列。"""
    assert set(hb.DEFAULT_PLATFORMS) == {"boss", "zhilian", "indeed", "glassdoor"}
    assert "linkedin" not in hb.DEFAULT_PLATFORMS
    assert "monster" not in hb.DEFAULT_PLATFORMS


def test_build_cmd_works_for_jobspy_and_cdp_platforms():
    """所有默认平台命令构造一致（spider 层各自消费 history_days）。"""
    for platform in hb.DEFAULT_PLATFORMS:
        cmd = hb.build_cmd(platform, days=90, max_pages=50, output_file=Path("x.jsonl"))
        assert cmd[cmd.index("crawl") + 1] == platform
        assert "-a" in cmd and "history_days=90" in cmd
        assert "-a" in cmd and "max_pages=50" in cmd


# ---------- _line_count ----------

def test_line_count_counts_jsonl_lines(tmp_path):
    f = tmp_path / "out.jsonl"
    f.write_text("a\nb\n\nc\n", encoding="utf-8")
    assert hb._line_count(f) == 4


def test_line_count_missing_file_zero():
    assert hb._line_count(Path("no_such_file.jsonl")) == 0


# ---------- run_scrapy ----------

@pytest.mark.asyncio
async def test_run_scrapy_success_counts_lines(tmp_path):
    out = tmp_path / "out.jsonl"
    out.write_text("{\"a\":1}\n{\"a\":2}\n", encoding="utf-8")

    class _Proc:
        returncode = 0

        async def communicate(self):
            return (b"", b"")

    async def _exec(*args, **kwargs):
        return _Proc()

    with mock.patch.object(hb.asyncio, "create_subprocess_exec", _exec):
        result = await hb.run_scrapy(["scrapy"], output_file=out)

    assert result["returncode"] == 0
    assert result["items"] == 2
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_run_scrapy_failure_when_nonzero_returncode(tmp_path):
    out = tmp_path / "out.jsonl"

    class _Proc:
        returncode = 1

        async def communicate(self):
            return (b"", b"boom error")

    async def _exec(*args, **kwargs):
        return _Proc()

    with mock.patch.object(hb.asyncio, "create_subprocess_exec", _exec):
        result = await hb.run_scrapy(["scrapy"], output_file=out)

    assert result["ok"] is False
    assert result["returncode"] == 1
    assert "boom" in result["error"]


@pytest.mark.asyncio
async def test_run_scrapy_failure_when_zero_items(tmp_path):
    out = tmp_path / "out.jsonl"  # 文件不存在 → 0 条

    class _Proc:
        returncode = 0

        async def communicate(self):
            return (b"", b"")

    async def _exec(*args, **kwargs):
        return _Proc()

    with mock.patch.object(hb.asyncio, "create_subprocess_exec", _exec):
        result = await hb.run_scrapy(["scrapy"], output_file=out)

    assert result["ok"] is False  # 0 产出视为失败（对齐 crawl_platform）


@pytest.mark.asyncio
async def test_run_scrapy_timeout_kills_proc(tmp_path):
    """子进程超时：kill 进程并返回失败（避免无限阻塞拖停多平台回爬）。"""
    out = tmp_path / "out.jsonl"

    killed = []

    class _Proc:
        returncode = -1

        async def communicate(self):
            await asyncio.sleep(10)  # 模拟卡死，远超 timeout=300

        def kill(self):
            killed.append(True)

        async def wait(self):
            return None

    async def _exec(*args, **kwargs):
        return _Proc()

    with mock.patch.object(hb.asyncio, "create_subprocess_exec", _exec):
        # 用短超时验证分支（不改常量，直接让 wait_for 参数生效）
        with mock.patch.object(hb, "SUBPROCESS_TIMEOUT", 0.01):
            result = await hb.run_scrapy(["scrapy"], output_file=out)

    assert result["ok"] is False
    assert result["returncode"] == -1
    assert killed == [True]
    assert "超时" in result["error"]


# ---------- backfill 一个平台的退避重试 ----------


def _patch_sleep_and_run(monkeypatch, run_scrapy_impl) -> list:
    """mock asyncio.sleep（记录延迟）与 run_scrapy，返回 sleep 延迟序列。"""
    sleeps: list = []

    async def _no_sleep(*a, **k):
        sleeps.append(a[0] if a else k.get("delay"))

    monkeypatch.setattr(hb.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(hb, "run_scrapy", run_scrapy_impl)
    return sleeps


async def _always_fail_run(cmd, output_file):
    return {"returncode": 1, "items": 0, "ok": False, "error": "blocked"}


@pytest.mark.asyncio
async def test_backfill_platform_retries_then_alert(monkeypatch, tmp_path):
    """单源连续失败：重试 MAX_ATTEMPTS 次 → 告警 → 不再重试。"""
    sleeps = _patch_sleep_and_run(monkeypatch, _always_fail_run)
    alerts = []

    async def _fake_alert(*a, **kw):
        alerts.append((a, kw))
        return True

    monkeypatch.setattr(hb, "send_alert", _fake_alert)

    result = await hb._backfill_platform(
        "boss", days=90, max_pages=50, out_dir=tmp_path,
    )

    assert result["status"] == "failed"
    assert result["attempts"] == hb.MAX_ATTEMPTS
    assert len(alerts) == 1  # 最终失败才告警
    event = alerts[0][0][0]
    assert event == "crawl_failed"
    # 重试之间按 backoff_delay 退避
    assert sleeps == [30, 60]  # MAX_ATTEMPTS=3 → 第 1、2 次失败后退避 30/60


@pytest.mark.asyncio
async def test_backfill_platform_recovers_on_second_attempt(monkeypatch, tmp_path):
    """第 1 次失败、第 2 次成功：结果成功，不告警。"""
    calls = {"n": 0}

    async def _recover_run(cmd, output_file):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"returncode": 1, "items": 0, "ok": False, "error": "blocked"}
        return {"returncode": 0, "items": 1, "ok": True, "error": ""}

    _patch_sleep_and_run(monkeypatch, _recover_run)
    alerts = []

    async def _fake_alert(*a, **kw):
        alerts.append((a, kw))
        return True

    monkeypatch.setattr(hb, "send_alert", _fake_alert)

    result = await hb._backfill_platform(
        "boss", days=90, max_pages=50, out_dir=tmp_path,
    )

    assert result["status"] == "success"
    assert result["attempts"] == 2
    assert result["items"] == 1
    assert alerts == []


# ---------- 报告写入 ----------

def test_write_report_creates_json(tmp_path):
    report = {
        "run_date": "2026-08-10",
        "platforms": {"boss": {"status": "success", "items": 10}},
    }
    path = hb._write_report(report, tmp_path)
    assert path.exists()
    import json

    assert json.loads(path.read_text(encoding="utf-8"))["platforms"]["boss"]["items"] == 10
