"""propose() 三域编排测试（第六轮审查 P1-5 回归锁）。

历史缺陷：propose() 只编排 position/skill 两域，别名回写仅有手动脚本——
ETL 阶段 20 不产出别名候选，动态别名表无新增来源。锁定：
1. domain="all" 时三域全部执行且别名域拿到独立预算（limit//4）；
2. domain="alias" 单域执行，其余跳过；
3. 摘要含 alias 分组（worker/日志消费方依赖）。
"""

import asyncio

from app.services.llm_decision import propose_normalization as pn


def _patch_chains(monkeypatch):
    """绕过 LLM 配置初始化（propose 内部 import 后构造 LLMProviderChain）。"""
    monkeypatch.setattr(
        "app.services.extraction.llm_provider.LLMProviderChain", object,
    )
    monkeypatch.setattr(pn, "_provider_of", lambda llm: ("fake", "fake-model"))


class TestProposeAliasDomain:
    def test_all_domain_runs_three_domains_with_alias_budget(self, monkeypatch):
        _patch_chains(monkeypatch)
        calls: dict = {}

        async def _fake_position(llm, recaller, provider, model, run_date, budget):
            calls["position"] = budget
            return {"candidates": 0, "proposed": 0, "blocked": 0, "llm_failed": 0,
                    "recall_mode": ""}

        async def _fake_skill(llm, provider, model, run_date, budget):
            calls["skill"] = budget
            return {"candidates": 0, "proposed": 0, "blocked": 0, "llm_failed": 0}

        async def _fake_alias(llm, provider, model, run_date, budget):
            calls["alias"] = budget
            return {"candidates": 0, "proposed": 0, "blocked": 0,
                    "llm_failed": 0, "low_conf": 0}

        def _fake_pool(driver):
            return []

        monkeypatch.setattr(pn, "propose_position", _fake_position)
        monkeypatch.setattr(pn, "propose_skill", _fake_skill)
        monkeypatch.setattr(pn, "propose_skill_alias", _fake_alias)
        monkeypatch.setattr(pn, "_existing_positions", _fake_pool)
        monkeypatch.setattr(
            "app.services.llm_decision.position_name.PositionCandidateRecaller",
            lambda pool, n: object(),
        )

        summary = asyncio.run(pn.propose(limit=40, domain="all"))

        assert summary["status"] == "ok"
        assert set(calls) == {"position", "skill", "alias"}
        assert calls["position"] == 20
        assert calls["alias"] == 10
        assert calls["skill"] == 10
        assert "alias" in summary

    def test_alias_only_domain_skips_others(self, monkeypatch):
        _patch_chains(monkeypatch)
        calls: list[str] = []

        async def _unexpected(*args, **kwargs):
            calls.append("unexpected")
            return {}

        async def _fake_alias(llm, provider, model, run_date, budget):
            calls.append(f"alias:{budget}")
            return {"candidates": 0, "proposed": 0, "blocked": 0,
                    "llm_failed": 0, "low_conf": 0}

        monkeypatch.setattr(pn, "propose_position", _unexpected)
        monkeypatch.setattr(pn, "propose_skill", _unexpected)
        monkeypatch.setattr(pn, "propose_skill_alias", _fake_alias)

        summary = asyncio.run(pn.propose(limit=40, domain="alias"))

        assert calls == ["alias:10"]
        assert summary["alias"]["proposed"] == 0
