"""backfill_jd_detail 回填脚本测试。

覆盖：_fetch_zhilian_detail（详情页 URL 构造 + 正文解析）与 backfill_zhilian
主循环（成功写回并清 extraction / keep-extraction 保留 / 抓取失败跳过 /
无正文跳过 / dry-run 不写库）。不触碰真实网络与 DB（monkeypatch 依赖）。
"""

import asyncio
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))

import pytest

from scripts import backfill_jd_detail as bf

_STATE = {"jobDetail": {"detailedPosition": {
    "description": "<div> 岗位职责:</div><div> 负责开发</div>"
                   "<div> 任职要求:</div><div> 本科以上</div>",
}}}
_HTML = f"<html><script>__INITIAL_STATE__={json.dumps(_STATE, ensure_ascii=False)}</script></html>"


class _FakeResp:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


class _FakeClient:
    """记录请求 URL，返回固定详情页 HTML（fake async client）。"""

    def __init__(self, html: str = _HTML):
        self.html = html
        self.url: str | None = None

    async def get(self, url, headers=None, timeout=None):
        self.url = url
        return _FakeResp(self.html)


def _row(row_id: int, source_id: str, snapshot: dict) -> tuple:
    return (row_id, source_id, "raw-text", snapshot)


async def _no_sleep(_seconds):
    return None


@pytest.mark.asyncio
async def test_fetch_zhilian_detail_builds_url_and_parses():
    """详情请求 URL 含 source_id，正文正确解析为职责/要求两段。"""
    client = _FakeClient()
    result = await bf._fetch_zhilian_detail(client, "CC0001")
    assert client.url == "https://www.zhaopin.com/jobdetail/CC0001.htm"
    assert result["description"] == "岗位职责:\n负责开发"
    assert result["requirements"] == "任职要求:\n本科以上"


@pytest.mark.asyncio
async def test_backfill_updates_and_clears_extraction(monkeypatch):
    """成功回填：snapshot 写入正文、删除旧 extraction 标记、raw_text 追加。"""
    rows = [
        _row(1, "CC1", {"description": "", "extraction": {"position_name": "X"}}),
        _row(2, "CC2", {"description": "", "extraction": {"position_name": "Y"}}),
    ]
    async def fake_pending(limit):
        return rows
    monkeypatch.setattr(bf, "_pending_rows", fake_pending)
    async def fake_fetch(client, source_id):
        return {"description": f"职责{source_id}", "requirements": f"要求{source_id}"}
    monkeypatch.setattr(bf, "_fetch_zhilian_detail", fake_fetch)
    applied: list[tuple[int, dict, str]] = []
    async def fake_apply(row_id, snapshot, raw_text):
        applied.append((row_id, snapshot, raw_text))
    monkeypatch.setattr(bf, "_apply", fake_apply)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    result = await bf.backfill_zhilian(limit=0, dry_run=False, keep_extraction=False)

    assert result["updated"] == 2
    assert result["failed"] == []
    row_id, snapshot, raw_text = applied[0]
    assert row_id == 1
    assert snapshot["description"] == "职责CC1"
    assert snapshot["requirements"] == "要求CC1"
    assert "extraction" not in snapshot  # 默认触发重抽
    assert raw_text == "raw-text\n职责CC1\n要求CC1"


@pytest.mark.asyncio
async def test_backfill_keep_extraction_preserves_marker(monkeypatch):
    """--keep-extraction 时保留旧 extraction 标记（仅补正文不重抽）。"""
    rows = [_row(1, "CC1", {"description": "", "extraction": {"position_name": "X"}})]
    async def fake_pending(limit):
        return rows
    monkeypatch.setattr(bf, "_pending_rows", fake_pending)
    async def fake_fetch(client, source_id):
        return {"description": "职责", "requirements": "要求"}
    monkeypatch.setattr(bf, "_fetch_zhilian_detail", fake_fetch)
    applied: list[tuple[int, dict, str]] = []
    async def fake_apply(row_id, snapshot, raw_text):
        applied.append((row_id, snapshot, raw_text))
    monkeypatch.setattr(bf, "_apply", fake_apply)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    await bf.backfill_zhilian(limit=0, dry_run=False, keep_extraction=True)

    assert applied[0][1]["extraction"] == {"position_name": "X"}


@pytest.mark.asyncio
async def test_backfill_fetch_failure_skips_row(monkeypatch):
    """单条详情抓取异常时跳过该条并记录 failed，不中断整体。"""
    rows = [_row(1, "CC1", {"description": ""}), _row(2, "CC2", {"description": ""})]
    async def fake_pending(limit):
        return rows
    monkeypatch.setattr(bf, "_pending_rows", fake_pending)
    calls = {"n": 0}
    async def fake_fetch(client, source_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("timeout")
        return {"description": "职责", "requirements": "要求"}
    monkeypatch.setattr(bf, "_fetch_zhilian_detail", fake_fetch)
    applied: list[int] = []
    async def fake_apply(row_id, snapshot, raw_text):
        applied.append(row_id)
    monkeypatch.setattr(bf, "_apply", fake_apply)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    result = await bf.backfill_zhilian(limit=0, dry_run=False, keep_extraction=False)

    assert result["updated"] == 1
    assert len(result["failed"]) == 1
    assert result["failed"][0]["id"] == 1
    assert applied == [2]


@pytest.mark.asyncio
async def test_backfill_empty_detail_skips_row(monkeypatch):
    """详情页无正文（SSR 缺失）时跳过并记录 failed。"""
    rows = [_row(1, "CC1", {"description": ""})]
    async def fake_pending(limit):
        return rows
    monkeypatch.setattr(bf, "_pending_rows", fake_pending)
    async def fake_fetch(client, source_id):
        return {"description": "", "requirements": ""}
    monkeypatch.setattr(bf, "_fetch_zhilian_detail", fake_fetch)
    async def fake_apply(row_id, snapshot, raw_text):
        return None
    monkeypatch.setattr(bf, "_apply", fake_apply)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    result = await bf.backfill_zhilian(limit=0, dry_run=False, keep_extraction=False)

    assert result["updated"] == 0
    assert "无正文" in result["failed"][0]["error"]


@pytest.mark.asyncio
async def test_backfill_dry_run_skips_writes(monkeypatch):
    """--dry-run 仅统计待回填条数，不写库。"""
    rows = [_row(1, "CC1", {"description": ""})]
    async def fake_pending(limit):
        return rows
    monkeypatch.setattr(bf, "_pending_rows", fake_pending)
    called = {"fetch": False, "apply": False}
    async def fake_fetch(client, source_id):
        called["fetch"] = True
        return {"description": "职责", "requirements": "要求"}
    async def fake_apply(row_id, snapshot, raw_text):
        called["apply"] = True
    monkeypatch.setattr(bf, "_fetch_zhilian_detail", fake_fetch)
    monkeypatch.setattr(bf, "_apply", fake_apply)

    result = await bf.backfill_zhilian(limit=0, dry_run=True, keep_extraction=False)

    assert result["pending"] == 1
    assert result["updated"] == 0
    assert not called["fetch"]
    assert not called["apply"]
