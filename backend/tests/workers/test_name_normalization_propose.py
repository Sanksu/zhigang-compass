"""名称归一提议 worker 测试（ETL 阶段 20：gating + proposal 编排语义）。

对齐 test_name_normalization_shadow 模式：mock 依赖，验证开关默认关返回
skipped、开启后走 propose 核心（不重复测判定逻辑——那在决策器测试里）。
"""

import unittest.mock as mock

from app.workers.name_normalization_propose import name_normalization_propose_daily


class TestGating:
    def test_disabled_by_default_skips(self):
        """开关默认 False → skipped，不触碰任何依赖（含 LLM 配置）。"""
        with mock.patch("app.core.runtime_config.get", return_value=False):
            result = asyncio_run(name_normalization_propose_daily({}))
        assert result["status"] == "skipped"
        assert "reason" in result

    def test_enabled_delegates_to_core(self):
        """开关开启 → 调用 propose 核心并透传 max_candidates。"""
        fake_summary = {"status": "ok", "position": {}, "skill": {}}
        captured: dict = {}

        async def _fake_propose(limit, domain):
            captured["limit"] = limit
            captured["domain"] = domain
            return fake_summary

        def _fake_get(key, default=None):
            if key == "name_normalization_propose_enabled":
                return True
            if key == "name_normalization_propose_max_candidates":
                return 55
            return default

        with mock.patch("app.core.runtime_config.get", side_effect=_fake_get), \
             mock.patch(
                 "app.services.llm_decision.propose_normalization.propose",
                 new=_fake_propose,
             ):
            result = asyncio_run(name_normalization_propose_daily({}))
        assert result == fake_summary
        assert captured == {"limit": 55, "domain": "all"}


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


class TestShellCompat:
    def test_scripts_shell_reexports(self):
        """scripts 薄壳 re-export 核心符号（既有测试依赖 RISK_TIER/_input_hash/propose）。"""
        from scripts.propose_name_normalization import (
            DEFAULT_LIMIT, RISK_TIER, _input_hash, propose,
        )
        assert RISK_TIER == "R2"
        assert DEFAULT_LIMIT == 40
        assert callable(propose)
        assert _input_hash("skill", "Java") == _input_hash("skill", "Java")
