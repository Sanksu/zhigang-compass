"""dict-guard 每日评估任务测试：ARQ 注册 / 总开关跳过路径。

不触真实 Neo4j/PG/LLM：注册断言读 WorkerSettings；禁用路径在触碰任何
连接前返回 skipped。
"""

import pytest
from app.workers.dict_guard import dict_guard_daily
from app.workers.settings import WorkerSettings
from app.workers.tasks import dict_guard_daily as facade_dict_guard_daily


def test_dict_guard_registered_in_functions():
    assert dict_guard_daily in WorkerSettings.functions
    # 门面 re-export 与实现同一对象（ARQ 按 __qualname__ 匹配）
    assert facade_dict_guard_daily is dict_guard_daily


@pytest.mark.asyncio
async def test_disabled_flag_skips_before_any_io(monkeypatch):
    from app.core import runtime_config

    monkeypatch.setattr(
        runtime_config, "get",
        lambda k, d=None: False if k == "dict_guard_enabled" else d,
    )
    result = await dict_guard_daily({})
    assert result["status"] == "skipped"
    assert "dict_guard_enabled" in result["reason"]


class _FakeChain:
    """LLMProviderChain 桩：记录调用路径，可注入成功/失败结果。"""

    def __init__(self, outcome=None):
        self.outcome = outcome
        self.calls = []

    def call_with_fallback(self, prompt, response_model, **kwargs):
        self.calls.append(response_model)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return response_model(action="add_stopword", term="测试词", reason="r", confidence=0.9)


@pytest.mark.asyncio
async def test_evaluate_uses_fallback_chain(monkeypatch):
    """评估器走 call_with_fallback（30s×优先级链）而非 call_sync 同步单点（验收建议①）。"""
    from app.services.extraction import llm_provider
    from app.workers import dict_guard as dg

    fake = _FakeChain()
    monkeypatch.setattr(llm_provider, "LLMProviderChain", lambda: fake)
    ev = dg._DictGuardEvaluator()
    decision = await ev.evaluate({"term": "测试词", "kind": "suspect_skill"})
    assert decision is not None and decision.term == "测试词"
    assert fake.calls and fake.calls[0] is dg.DictGuardDecision


@pytest.mark.asyncio
async def test_evaluate_llm_failure_returns_none(monkeypatch):
    """LLM 全链失败降级为 None（本轮跳过该候选），不向管线抛异常。"""
    from app.services.extraction import llm_provider
    from app.workers import dict_guard as dg

    err = llm_provider.LLMExtractionError("全部 provider 失败")
    monkeypatch.setattr(llm_provider, "LLMProviderChain", lambda: _FakeChain(err))
    ev = dg._DictGuardEvaluator()
    assert await ev.evaluate({"term": "x", "kind": "suspect_skill"}) is None
