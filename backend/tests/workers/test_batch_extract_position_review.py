"""batch_extract 岗位名审查编排测试（默认关闭 / 触发审查 / invalid 拒绝入图）。

mock 数据库层 + JDExtractor + 审查 LLM（对齐 test_batch_extract_llm_outage
模式），batch_extract 编排与 _review_position_names 为真实产品代码。
"""

import asyncio
import unittest.mock as mock
from types import SimpleNamespace

from app.services.extraction.position_review import PositionReviewResult
from app.services.extraction.schemas import JDExtractionResult, SkillExtracted
from app.workers.tasks import batch_extract


def _jd_row(jd_id):
    return SimpleNamespace(
        id=jd_id,
        snapshot={},
        source="boss",
        source_url="http://x/jd",
        crawled_at="2026-08-23T00:00:00+08:00",
        raw_text="岗位描述" * 20,
    )


def _extraction(position_name, method="llm"):
    return JDExtractionResult(
        position_name=position_name,
        skills=[SkillExtracted(name="Python")],
        method=method,
    )


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalars(self, stmt):
        return _FakeResult(self._rows)

    async def execute(self, stmt):
        raise NotImplementedError("频次查询应被 mock 替换")

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


class _ReviewLLM:
    """extract_structured 桩：按 position_name 返回裁决。"""

    def __init__(self, verdicts: dict[str, PositionReviewResult]):
        self._verdicts = verdicts
        self.asked: list[str] = []

    def extract_structured(self, prompt, response_model, system_prompt=None, timeout=None):
        for name, verdict in self._verdicts.items():
            if f'"{name}"' in prompt:
                self.asked.append(name)
                return verdict
        return PositionReviewResult(valid=True, category="standard")


class _FakeExtractor:
    def __init__(self, names, llm=None):
        self._names = names
        self.llm = llm

    def extract_batch(self, texts, **kwargs):
        return [_extraction(n) for n in self._names[: len(texts)]]


def _run(rows, extractor, freqs=None, enabled=True):
    """执行 batch_extract；返回 (结果, session, import 调用次数)。"""
    calls = {"import": 0}

    def _fake_import(*a, **k):
        calls["import"] += 1
        return "pos_0001"

    session = _FakeSession(rows)

    with (
        mock.patch("app.core.database.async_session_factory", return_value=session),
        mock.patch("app.core.database.neo4j_driver", _FakeDriver()),
        mock.patch(
            "app.services.extraction.jd_extractor.JDExtractor",
            return_value=extractor,
        ),
        mock.patch("app.services.kg.kg_service.import_jd", side_effect=_fake_import),
        mock.patch(
            "app.workers.etl_tasks._position_frequencies",
            new=mock.AsyncMock(return_value=freqs or {}),
        ),
        mock.patch("app.core.runtime_config.get", side_effect=_cfg_getter(enabled)),
    ):
        result = asyncio.run(batch_extract({}, limit=100))
    return result, session, calls


def _cfg_getter(enabled):
    def _get(key, default=None):
        if key == "position_review_enabled":
            return enabled
        return default

    return _get


# 规则放行的未知名：含 ASCII（纯中文未知名会被白名单门拦截为空），
# 不命中任何关键词族/停用词/白名单
_UNKNOWN = "Zorp数据管理员"
_STANDARD = "前端开发工程师"  # 白名单族，不触发审查


class TestDisabledByDefault:
    def test_disabled_skips_review_entirely(self):
        rows = [_jd_row(1)]
        llm = _ReviewLLM({})
        extractor = _FakeExtractor([_UNKNOWN], llm=llm)
        result, session, calls = _run(rows, extractor, enabled=False)

        assert llm.asked == []
        assert result["position_reviews"] == 0
        assert calls["import"] == 1
        assert session.committed is True
        assert "position_review" not in rows[0].snapshot


class TestReviewWiring:
    def test_invalid_verdict_rejects_graph_import_and_audits(self):
        rows = [_jd_row(1)]
        llm = _ReviewLLM({_UNKNOWN: PositionReviewResult(
            valid=False, category="gibberish", reason="荒谬组合",
        )})
        extractor = _FakeExtractor([_UNKNOWN], llm=llm)
        result, session, calls = _run(rows, extractor, freqs={_UNKNOWN: 0})

        assert llm.asked == [_UNKNOWN]
        assert calls["import"] == 0  # 不入图
        assert result["review_rejected"] == 1
        assert result["succeeded"] == 0
        # 游标仍推进 + 审计记录落快照
        assert session.committed is True
        assert "extraction" in rows[0].snapshot
        review = rows[0].snapshot["position_review"]
        assert review["valid"] is False
        assert review["category"] == "gibberish"
        assert review["original"] == _UNKNOWN

    def test_valid_no_standard_keeps_original_and_imports(self):
        rows = [_jd_row(1)]
        llm = _ReviewLLM({_UNKNOWN: PositionReviewResult(valid=True, category="standard")})
        extractor = _FakeExtractor([_UNKNOWN], llm=llm)
        result, _, calls = _run(rows, extractor, freqs={_UNKNOWN: 2})

        assert calls["import"] == 1
        assert result["review_rejected"] == 0
        assert rows[0].snapshot["position_review"]["valid"] is True

    def test_standard_name_adoption_rewrites_position(self):
        rows = [_jd_row(1)]
        llm = _ReviewLLM({_UNKNOWN: PositionReviewResult(
            valid=True, category="generic",
            standard_name="机器视觉算法工程师",
        )})
        extractor = _FakeExtractor([_UNKNOWN], llm=llm)
        result, _, calls = _run(rows, extractor, freqs={_UNKNOWN: 0})

        assert calls["import"] == 1
        # 快照 normalized_position 采用修正名（persist_normalized_position 以改写后为准）
        assert rows[0].snapshot["normalized_position"] == "机器视觉算法工程师"
        assert rows[0].snapshot["position_review"]["standard_name"] == "机器视觉算法工程师"

    def test_highfreq_candidate_not_asked(self):
        rows = [_jd_row(1)]
        llm = _ReviewLLM({})
        extractor = _FakeExtractor([_STANDARD], llm=llm)  # 白名单族
        result, _, calls = _run(rows, extractor, freqs={})

        assert llm.asked == []
        assert result["position_reviews"] == 0
        assert calls["import"] == 1

    def test_llm_none_degrades_silently(self):
        rows = [_jd_row(1)]
        extractor = _FakeExtractor([_UNKNOWN], llm=None)  # 无 key 环境
        result, session, calls = _run(rows, extractor, freqs={_UNKNOWN: 0})

        assert result["position_reviews"] == 0
        assert result["review_rejected"] == 0
        assert calls["import"] == 1
        assert session.committed is True
