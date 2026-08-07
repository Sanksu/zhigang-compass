"""JDExtractor.extract_batch 单元测试（设计文档 §6.5 批量抽取优化）。

原型验证（test_batch_extract_proto.py 内嵌编排逻辑）已实装为
`JDExtractor.extract_batch`（jd_extractor.py），本文件为其实装后的契约测试：
- 批量路径：N 条分多批 → 批量 LLM 每批调用 1 次，降级 LLM 不触发
- 单条降级路径：整批失败 / 超时 / 返回条数错位 → 该批降级逐条 extract
- 动态长度封顶：batch_size 与 max_batch_chars 双约束组批
- 批量独立超时：batch_timeout 透传 LLM 链

mock 的是 LLM 链（LLMProviderChain 的 extract_structured），编排逻辑为真实产品代码。
"""

import pytest

from app.services.extraction.jd_extractor import JDExtractor
from app.services.extraction.llm_provider import LLMExtractionError
from app.services.extraction.schemas import (
    JDExtractionBatch,
    JDExtractionResult,
    SkillExtracted,
)


def _make_result(title: str) -> JDExtractionResult:
    return JDExtractionResult(
        position_name=title,
        skills=[SkillExtracted(name="Python")],
    )


class _FakeBatchLLM:
    """假 LLM 链：批量（JDExtractionBatch）返回预设 batch，单条返回单条结果。

    记录调用与 timeout；区分 response_model 以支撑降级逐条路径。
    """

    def __init__(self, batch: JDExtractionBatch):
        self.batch = batch
        self.calls = 0
        self.timeouts: list = []

    def extract_structured(self, prompt, response_model, **kwargs):
        self.calls += 1
        self.timeouts.append(kwargs.get("timeout"))
        if response_model is JDExtractionResult:
            return _make_result("单条")
        return self.batch


class _FailingBatchLLM:
    """批量调用恒抛错的假 LLM 链（整批失败 → 降级逐条，逐条走单条 extract）。"""

    def __init__(self, single_result: JDExtractionResult):
        self.single_result = single_result
        self.batch_calls = 0
        self.single_calls = 0

    def extract_structured(self, prompt, response_model, **kwargs):
        if response_model is JDExtractionBatch:
            self.batch_calls += 1
            raise LLMExtractionError("批量调用失败")
        # 降级逐条：单条走普通 extract 的 LLM 路径
        self.single_calls += 1
        return self.single_result


class _TimeoutBatchLLM(_FailingBatchLLM):
    """批量调用抛 LLMTimeoutError（超时与普通失败同降级语义）。"""

    def extract_structured(self, prompt, response_model, **kwargs):
        if response_model is JDExtractionBatch:
            self.batch_calls += 1
            raise LLMExtractionError("批量调用超时")  # LLMTimeoutError 是 LLMExtractionError 子类语义
        return super().extract_structured(prompt, response_model, **kwargs)


TEXTS = [
    "JD 文本 A：招聘 Python 开发，精通 Django",
    "JD 文本 B：招聘 Java 开发，熟悉 Spring Boot",
    "JD 文本 C：招聘前端，熟练 React",
    "JD 文本 D：招聘 Go 开发，了解 Kubernetes",
]


class TestBatchExtractPath:
    def test_batch_path_one_call_per_batch(self):
        """4 条分 2 批（batch_size=2）→ 批量 LLM 恰好调用 2 次。"""
        batch_llm = _FakeBatchLLM(JDExtractionBatch(results=[_make_result("批量1"), _make_result("批量2")]))
        out = JDExtractor(llm=batch_llm).extract_batch(TEXTS, batch_size=2)
        assert batch_llm.calls == 2
        assert len(out) == 4

    def test_batch_timeout_passed_to_llm(self):
        """batch_timeout 透传 LLM 链（批量独立超时，不动单条默认）。"""
        batch_llm = _FakeBatchLLM(JDExtractionBatch(results=[_make_result("批量1")]))
        JDExtractor(llm=batch_llm).extract_batch(TEXTS[:1], batch_timeout=90)
        assert batch_llm.timeouts == [90]

    def test_empty_input_no_llm_call(self):
        batch_llm = _FakeBatchLLM(JDExtractionBatch(results=[]))
        out = JDExtractor(llm=batch_llm).extract_batch([])
        assert batch_llm.calls == 0
        assert out == []


class TestSingleFallbackPath:
    def test_batch_failure_falls_back_to_single(self):
        """整批失败 → 该批逐条抽取（4 条逐条 4 次）。"""
        llm = _FailingBatchLLM(_make_result("单条"))
        out = JDExtractor(llm=llm).extract_batch(TEXTS, batch_size=2)
        assert llm.batch_calls == 2
        assert llm.single_calls == 4
        assert len(out) == 4

    def test_wrong_count_falls_back_to_single(self):
        """返回条数错位 → 判定张冠李戴风险，降级逐条。"""
        llm = _FakeBatchLLM(JDExtractionBatch(results=[_make_result("漏了一条")]))
        out = JDExtractor(llm=llm).extract_batch(TEXTS, batch_size=2)
        # 2 批各返回 1 条 ≠ 输入 2 条 → 两批降级逐条（批量 2 次 + 单条 4 次）
        assert llm.calls == 6
        assert len(out) == 4
        # 降级结果来自逐条 extract（单条 mock 返回 "单条"）
        assert [r.position_name for r in out] == ["单条"] * 4

    def test_timeout_falls_back_to_single(self):
        """批量超时（LLMTimeoutError）→ 该批降级逐条（与普通失败同语义）。"""
        llm = _TimeoutBatchLLM(_make_result("单条"))
        out = JDExtractor(llm=llm).extract_batch(TEXTS, batch_size=2)
        assert llm.batch_calls == 2
        assert llm.single_calls == 4
        assert len(out) == 4


class TestConcurrentBatches:
    """concurrency>1 并行路径：顺序保持、条数正确、失败降级。

    回归：LLM 生成时间由输出 token 总量决定，并发（而非调大 batch_size）
    才是吞吐瓶颈；并发须保持与串行完全一致的契约。
    """

    def test_concurrency_keeps_order_and_count(self):
        """4 条分 2 批（batch_size=2）并发 2 → 每批 1 次批量调用，返回 4 条且有序。"""
        batch = JDExtractionBatch(results=[_make_result("批量1"), _make_result("批量2")])
        out = JDExtractor(llm=_LockedBatchLLM(batch)).extract_batch(
            TEXTS, batch_size=2, concurrency=2
        )
        assert len(out) == 4

    def test_concurrency_equals_serial_results(self):
        """同一输入下并发结果与串行完全一致（顺序/内容）。"""
        batch = JDExtractionBatch(results=[_make_result("批量1"), _make_result("批量2")])
        serial = JDExtractor(llm=_FakeBatchLLM(batch)).extract_batch(TEXTS, batch_size=2)
        concurrent = JDExtractor(llm=_LockedBatchLLM(batch)).extract_batch(
            TEXTS, batch_size=2, concurrency=2
        )
        assert [r.position_name for r in concurrent] == [r.position_name for r in serial]

    def test_concurrency_failure_falls_back_to_single(self):
        """并发下整批失败仍降级逐条（每批内降级，批间并行）。"""
        llm = _FailingBatchLLM(_make_result("单条"))
        out = JDExtractor(llm=llm).extract_batch(TEXTS, batch_size=2, concurrency=2)
        assert len(out) == 4
        assert [r.position_name for r in out] == ["单条"] * 4


class _LockedBatchLLM(_FakeBatchLLM):
    """线程安全版假 LLM 链：锁保护调用计数，供并发测试断言（防 += 竞态丢计数）。"""

    def __init__(self, batch: JDExtractionBatch):
        super().__init__(batch)
        import threading

        self._lock = threading.Lock()

    def extract_structured(self, prompt, response_model, **kwargs):
        with self._lock:
            self.calls += 1
            self.timeouts.append(kwargs.get("timeout"))
        if response_model is JDExtractionResult:
            return _make_result("单条")
        return self.batch


class TestDynamicBatching:
    LONG_TEXTS = [
        "JD 文本 A：" + "很长很长的 JD 正文" * 50,  # ~800 字符
        "JD 文本 B：" + "很长很长的 JD 正文" * 50,
        "JD 文本 C：" + "很长很长的 JD 正文" * 50,
    ]

    def test_chars_cap_splits_batch(self):
        """batch_size 大但 max_batch_chars 小 → 按长度切批（3 条 3 批）。"""
        batch_llm = _FakeBatchLLM(JDExtractionBatch(results=[_make_result("批量1")]))
        out = JDExtractor(llm=batch_llm).extract_batch(
            self.LONG_TEXTS, batch_size=10, max_batch_chars=1000
        )
        assert batch_llm.calls == 3
        assert len(out) == 3

    def test_chars_cap_within_batch_size(self):
        """双约束共存：不超长时按 batch_size 切。"""
        batch_llm = _FakeBatchLLM(JDExtractionBatch(results=[_make_result("批量1"), _make_result("批量2")]))
        out = JDExtractor(llm=batch_llm).extract_batch(
            TEXTS, batch_size=2, max_batch_chars=100_000
        )
        assert batch_llm.calls == 2
        assert len(out) == 4

    def test_no_chars_cap_means_batch_size_only(self):
        batch_llm = _FakeBatchLLM(JDExtractionBatch(results=[_make_result("批量1")] * 3))
        JDExtractor(llm=batch_llm).extract_batch(self.LONG_TEXTS, batch_size=3)
        assert batch_llm.calls == 1
