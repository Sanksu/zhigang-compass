"""batch_extract 入图失败重试 + 低质 JD 跳过 测试（爬虫入图审计 P0-1/P1-3）。

- P0-1：入图（import_jd）失败时不写 extraction 标记（保持 `extraction IS NULL`，
  下次批跑自动重试），错误写入 snapshot["extraction_error"] 落库审计
- P1-3：needs_review（质量 < 0.6）记录不进 LLM 抽取，写 skipped 标记推进游标
- 成功路径回归：入图成功才写 extraction

mock 数据库层（async_session_factory / neo4j_driver）与 LLM/入图服务，
batch_extract 编排逻辑为真实产品代码。
"""

import asyncio
import unittest.mock as mock
from types import SimpleNamespace

from app.services.extraction.position_normalization import POSITION_NORMALIZATION_VERSION
from app.services.extraction.schemas import JDExtractionResult, SkillExtracted
from app.workers.tasks import batch_extract


def _jd_row(jd_id, snapshot=None, source="boss", source_url="http://x/jd", raw_text=None):
    return SimpleNamespace(
        id=jd_id,
        snapshot=dict(snapshot or {}),
        source=source,
        source_url=source_url,
        crawled_at="2026-08-08T00:00:00+08:00",
        raw_text=raw_text or ("岗位描述" * 20),  # 足够长，不触发短文本跳过
    )


def _extraction(position_name="Python 开发工程师"):
    return JDExtractionResult(
        position_name=position_name,
        skills=[SkillExtracted(name="Python")],
    )


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    """AsyncSession fake：scalars 返回预置行，记录是否 commit。"""

    def __init__(self, rows):
        self._rows = rows
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalars(self, stmt):
        return _FakeResult(self._rows)

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


def _run(rows, extractions=None, import_jd_side_effect=None):
    """patch 数据库层 + LLM/入图服务后执行 batch_extract。"""

    class _FakeExtractor:
        llm = None  # 与 JDExtractor 接口对齐（本文件场景不经过 LLM 全败检测）

        def extract_batch(self, texts, **kwargs):
            # 按 valid 行数返回抽取结果（与编排契约一致）
            return [extractions[i] for i in range(len(texts))] if extractions else []

    def _factory():
        return _FakeSession(rows)

    with (
        mock.patch("app.core.database.async_session_factory", side_effect=_factory),
        mock.patch("app.core.database.neo4j_driver", _FakeDriver()),
        mock.patch("app.services.extraction.jd_extractor.JDExtractor", return_value=_FakeExtractor()),
        mock.patch(
            "app.services.kg.kg_service.import_jd",
            side_effect=import_jd_side_effect or (lambda *a, **k: "pos_0001"),
        ),
    ):
        return asyncio.run(batch_extract({}, limit=100))


class TestImportFailureRetryable:
    def test_failed_import_keeps_extraction_null_for_retry(self):
        """入图失败 → extraction 不写入（保持 IS NULL 可重试），错误落库审计。"""
        rows = [_jd_row(1)]

        def _fail(*args, **kwargs):
            raise RuntimeError("Neo4j 连接失败")

        result = _run(rows, extractions=[_extraction()], import_jd_side_effect=_fail)

        assert result["succeeded"] == 0
        assert result["failed"] == [{"jd_id": 1, "error": "Neo4j 连接失败"}]
        # 关键：extraction 保持未写入（下次批跑 `extraction IS NULL` 仍选中重试）
        assert "extraction" not in rows[0].snapshot
        # 审计：错误留痕
        assert rows[0].snapshot["extraction_error"] == "Neo4j 连接失败"

    def test_success_writes_extraction_after_import(self):
        """入图成功 → 才写 extraction 标记（含 method 字段），不计入 failed。"""
        rows = [_jd_row(1)]
        result = _run(rows, extractions=[_extraction()])

        assert result["succeeded"] == 1
        assert result["failed"] == []
        # 保留模型原始岗位名用于审计，规范岗位名单独持久化供下游消费。
        assert rows[0].snapshot["extraction"]["position_name"] == "Python 开发工程师"
        assert rows[0].snapshot["normalized_position"] == "Python开发工程师"
        assert rows[0].snapshot["normalized_position_meta"] == {
            "version": POSITION_NORMALIZATION_VERSION
        }
        assert rows[0].snapshot["extraction"]["method"] == "llm"

    def test_partial_failure_only_marks_succeeded_rows(self):
        """混合场景：第 1 条入图失败、第 2 条成功 → 仅成功条写 extraction。"""
        rows = [_jd_row(1), _jd_row(2)]

        def _flaky(*args, **kwargs):
            # 按 evidence raw_text 无法区分，改按调用次数：第一次抛、第二次成功
            _flaky.calls = getattr(_flaky, "calls", 0) + 1
            if _flaky.calls == 1:
                raise RuntimeError("首次失败")
            return "pos_0002"

        result = _run(rows, extractions=[_extraction("A"), _extraction("B")], import_jd_side_effect=_flaky)

        assert result["succeeded"] == 1
        assert len(result["failed"]) == 1
        assert "extraction" not in rows[0].snapshot
        assert rows[0].snapshot["extraction_error"] == "首次失败"
        assert rows[1].snapshot["extraction"]["position_name"] == "B"


class TestNeedsReviewSkipped:
    def test_needs_review_row_skipped_not_extracted(self):
        """needs_review=True → 不抽取，写 skipped 标记推进游标，failed 记录原因。"""
        rows = [_jd_row(1, snapshot={"needs_review": True})]

        # extract_batch 不应收到任何文本（无 valid 行）；入图不应被调用
        result = _run(rows)

        assert result["processed"] == 0
        assert result["failed"] == [{"jd_id": 1, "error": "质量评分 < 0.6，跳过"}]
        assert rows[0].snapshot["extraction"] == {
            "skipped": True,
            "reason": "质量评分 < 0.6，需人工复核",
        }

    def test_review_row_and_normal_row_mixed(self):
        """低质行跳过，正常行照常抽取入图。"""
        rows = [
            _jd_row(1, snapshot={"needs_review": True}),
            _jd_row(2),
        ]
        result = _run(rows, extractions=[_extraction("B")])

        assert result["succeeded"] == 1
        assert rows[0].snapshot["extraction"]["skipped"] is True
        assert rows[1].snapshot["extraction"]["position_name"] == "B"
