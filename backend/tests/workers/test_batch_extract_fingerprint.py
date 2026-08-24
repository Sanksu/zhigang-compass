"""batch_extract 内容指纹重抽测试（PR2b：重爬不重抽的语义滞后治理）。

场景：
- 已抽取行 content_hash 为空（存量回填）→ 只补指纹不重抽
- 已抽取行指纹与正文不一致（重爬更新）→ 重抽，旧产物留 extraction_prev
- 指纹一致 → 跳过重抽
- 显式 jd_ids 不触发指纹重抽

mock 数据库层与 JDExtractor（对齐 test_batch_extract_llm_outage 模式）。
"""

import asyncio
import unittest.mock as mock
from types import SimpleNamespace

from app.services.extraction.schemas import JDExtractionResult, SkillExtracted
from app.workers.etl_tasks import _content_hash
from app.workers.tasks import batch_extract


def _jd_snapshot(text: str) -> dict:
    return {"title": "Java 后端开发", "description": text}


def _extraction_result() -> JDExtractionResult:
    return JDExtractionResult(
        position_name="Java 后端开发工程师",
        skills=[SkillExtracted(name="Java")],
        method="llm",
    )


class _StepSession:
    """scalars 按调用顺序返回不同行集（首次=未抽取游标，二次=指纹重抽游标）。"""

    def __init__(self, unextracted: list, recheck: list):
        self._steps = [list(unextracted), list(recheck)]
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalars(self, stmt):
        return SimpleNamespace(all=lambda: self._steps.pop(0))

    async def commit(self):
        self.committed = True


class _FakeNeo4jSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    def session(self):
        return _FakeNeo4jSession()


class _CaptureExtractor:
    llm = object()

    def __init__(self):
        self.calls: list[str] = []

    def extract_batch(self, texts, **kwargs):
        self.calls.extend(texts)
        return [_extraction_result() for _ in texts]


def _run(unextracted, recheck):
    extractor = _CaptureExtractor()
    session = _StepSession(unextracted, recheck)
    with (
        mock.patch("app.core.database.async_session_factory", return_value=session),
        mock.patch("app.core.database.neo4j_driver", _FakeDriver()),
        mock.patch(
            "app.services.extraction.jd_extractor.JDExtractor",
            return_value=extractor,
        ),
        mock.patch("app.services.kg.kg_service.import_jd", return_value="pos_0001"),
    ):
        result = asyncio.run(batch_extract({}, limit=100))
    return result, session, extractor


class TestContentHashHelper:
    def test_deterministic_and_sensitive(self):
        snap = _jd_snapshot("负责后端开发")
        h1 = _content_hash(snap, "RAW")
        h2 = _content_hash(snap, "RAW")
        assert h1 == h2
        assert len(h1) == 64
        assert _content_hash(_jd_snapshot("负责后端开发与架构"), "RAW") != h1

    def test_built_text_driven(self):
        # 只改 raw_text（正文 fallback 不参与拼装时）不改变指纹
        snap = _jd_snapshot("负责后端开发")
        assert _content_hash(snap, "A") == _content_hash(snap, "B")


def _fingerprint_row(jd_id: int, snapshot: dict, content_hash: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=jd_id,
        snapshot=snapshot,
        raw_text="RAW",
        source="boss",
        source_url="http://x/jd",
        crawled_at="2026-08-23T00:00:00+08:00",
        content_hash=content_hash,
    )


class TestReExtraction:
    def test_changed_content_re_extracts_and_keeps_prev(self):
        old_snap = _jd_snapshot("负责后端开发")
        new_snap = _jd_snapshot("负责后端开发与微服务架构")
        # 重爬已更新正文快照，但存量指纹仍是旧正文的哈希 → 指纹不一致触发重抽
        recheck = [_fingerprint_row(
            10, {**new_snap, "extraction": {"method": "llm", "position_name": "旧结果"}},
            _content_hash(old_snap, "RAW"),
        )]
        result, session, extractor = _run([], recheck)
        assert result["re_extracted"] == 1
        assert result["succeeded"] == 1
        assert session.committed is True
        assert len(extractor.calls) == 1
        row = recheck[0]
        # 旧产物留档审计
        assert row.snapshot["extraction_prev"]["position_name"] == "旧结果"
        # 指纹刷新为最新正文
        assert row.content_hash == _content_hash(new_snap, "RAW")
        assert row.snapshot["extraction"]["position_name"] == "Java 后端开发工程师"

    def test_backfill_only_no_reextract(self):
        snap = _jd_snapshot("负责后端开发")
        recheck = [_fingerprint_row(11, {**snap, "extraction": {"method": "llm"}}, "")]
        result, session, extractor = _run([], recheck)
        assert result["content_hash_backfilled"] == 1
        assert result["re_extracted"] == 0
        assert extractor.calls == []
        assert recheck[0].content_hash == _content_hash(snap, "RAW")

    def test_unchanged_content_skipped(self):
        snap = _jd_snapshot("负责后端开发")
        fingerprint = _content_hash(snap, "RAW")
        recheck = [_fingerprint_row(12, {**snap, "extraction": {"method": "llm"}}, fingerprint)]
        result, _, extractor = _run([], recheck)
        assert result["re_extracted"] == 0
        assert extractor.calls == []
        assert "extraction_prev" not in recheck[0].snapshot


class TestCursorRowFingerprint:
    def test_unextracted_row_gets_fingerprint_on_extraction(self):
        snap = _jd_snapshot("负责后端开发")
        unextracted = [SimpleNamespace(
            id=1, snapshot=dict(snap), raw_text="RAW",
            source="boss", source_url="http://x/jd", crawled_at="2026-08-23T00:00:00+08:00",
            content_hash="",
        )]
        result, _, _ = _run(unextracted, [])
        assert result["succeeded"] == 1
        assert unextracted[0].content_hash == _content_hash(snap, "RAW")
        assert result["re_extracted"] == 0