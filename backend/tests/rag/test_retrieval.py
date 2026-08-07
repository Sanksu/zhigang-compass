"""通用 RAG 检索模块单元测试（设计文档 §6.4 节）。

覆盖：token 估算、四类检索源（岗位定义/权威库/技能/历史诊断）的组装、
evidence_id 追溯、token 截断、空查询兜底。
"""

import asyncio

import pytest

from app.models.business import DiagnosisReportRecord, DiscoveryCandidate, Occupation
from app.services.rag.retrieval import (
    _diagnoses,
    _estimate_tokens,
    _verified_positions,
    retrieve_context,
)


def _candidate(name, state="emerging", definition="负责岗位定义。", **kw):
    return DiscoveryCandidate(
        position_name=name, state=state, definition_draft=definition, **kw
    )


def _occupation(code="15-1252.00", name="Software Developers", definition="Design software."):
    return Occupation(
        code=code,
        name=name,
        category="Computer and Mathematical",
        definition=definition,
        aliases=[],
    )


def _diagnosis_record(match_id="m1", position_name="数据分析师", summary="总体匹配度较高", gaps=None):
    return DiagnosisReportRecord(
        match_id=match_id,
        position_name=position_name,
        report={
            "overall_summary": summary,
            "top_gaps": gaps or [{"skill": "Python", "advice": "补齐 Python 编程"}],
        },
    )


class _FakeDb:
    """按调用次序返回批次的假 AsyncSession（scalars → all 消费一批）。"""

    def __init__(self, *batches):
        self._batches = list(batches)
        self._cur = []
        self.calls = 0

    async def scalars(self, stmt):
        self.calls += 1
        self._cur = self._batches.pop(0) if self._batches else []
        return self

    def all(self):
        return self._cur


class TestEstimateTokens:
    def test_cjk_counts_one_per_char(self):
        assert _estimate_tokens("岗位定义") == 4

    def test_latin_four_chars_per_token(self):
        assert _estimate_tokens("abcdefgh") == 2

    def test_mixed(self):
        assert _estimate_tokens("岗位 Software") == 5  # 2 中文 + 9 拉丁(4 字符/token)


class TestVerifiedPositions:
    def test_skips_empty_definition(self):
        """空定义跳过（状态/ILike 过滤在 SQL 层，由假 db 不模拟）。"""
        async def _run():
            db = _FakeDb(
                [
                    _candidate("AI 工程师", state="emerging"),
                    _candidate("无定义", state="stable", definition=""),
                ]
            )
            chunks = await _verified_positions(db, "AI 工程师")
            assert len(chunks) == 1
            assert chunks[0].evidence_id == "position:AI 工程师"
            assert chunks[0].source == "position_definition"

        asyncio.run(_run())

    def test_empty_query_returns_empty(self):
        async def _run():
            assert await _verified_positions(_FakeDb(), "") == []

        asyncio.run(_run())


class TestDiagnoses:
    def test_builds_summary_with_gap_advice(self):
        async def _run():
            db = _FakeDb(
                [
                    _diagnosis_record(
                        gaps=[
                            {"skill": "Python", "advice": "补齐 Python"},
                            {"skill": "SQL", "advice": "学习 SQL"},
                        ]
                    )
                ]
            )
            chunks = await _diagnoses(db, "数据分析")
            assert len(chunks) == 1
            assert chunks[0].evidence_id == "diagnosis:m1"
            assert chunks[0].source == "diagnosis"
            assert "总体匹配度较高" in chunks[0].content
            assert "Python:补齐 Python" in chunks[0].content

        asyncio.run(_run())

    def test_skips_report_without_summary(self):
        async def _run():
            db = _FakeDb([_diagnosis_record(summary="")])
            assert await _diagnoses(db, "数据分析") == []

        asyncio.run(_run())


class TestRetrieveContext:
    def test_merges_all_sources_with_evidence_id(self):
        """四类检索源命中合并，每 chunk 附 evidence_id。"""
        async def _run():
            db = _FakeDb(
                [_candidate("AI 工程师")],
                [_occupation()],
                [_diagnosis_record(position_name="AI 工程师", summary="总体匹配度中等")],
            )
            chunks = await retrieve_context("AI 工程师", db, neo4j=None)
            ids = {c.evidence_id for c in chunks}
            assert "position:AI 工程师" in ids
            assert "occupation:15-1252.00" in ids
            assert "diagnosis:m1" in ids
            assert all(c.content for c in chunks)

        asyncio.run(_run())

    def test_drops_chunk_exceeding_window(self):
        """上下文窗口截取（§6.4 3000 token 上限）：超限 chunk 整体丢弃。"""
        async def _run():
            db = _FakeDb(
                [_candidate("AI 工程师", definition="长" * 20)],  # 约 25 token
                [],
                [],
            )
            chunks = await retrieve_context("AI 工程师", db, neo4j=None, max_tokens=10)
            assert chunks == []

        asyncio.run(_run())

    def test_total_tokens_never_exceed_window(self):
        """多命中时累计 token 不超过窗口上限。"""
        async def _run():
            db = _FakeDb(
                [_candidate("AI 工程师", definition="岗位定义。")],
                [],
                [_diagnosis_record(position_name="AI 工程师", summary="总体匹配度中等")],
            )
            chunks = await retrieve_context("AI 工程师", db, neo4j=None, max_tokens=100)
            total = sum(_estimate_tokens(c.content) for c in chunks)
            assert total <= 100
            assert {c.source for c in chunks} == {"position_definition", "diagnosis"}

        asyncio.run(_run())

    def test_empty_query_returns_empty(self):
        async def _run():
            assert await retrieve_context("", _FakeDb(), neo4j=None) == []

        asyncio.run(_run())

    def test_no_hits_returns_empty(self):
        async def _run():
            db = _FakeDb([], [], [])
            assert await retrieve_context("焊工", db, neo4j=None) == []

        asyncio.run(_run())
