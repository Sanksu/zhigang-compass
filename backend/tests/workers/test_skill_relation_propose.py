"""技能关系提议 worker 测试（ETL 阶段 21：gating + 薄壳兼容）。

对齐 test_name_normalization_propose 模式：mock 依赖验证开关语义；
判定/候选逻辑在决策器与编排核心测试里（tests/scripts/test_propose_skill_relations.py）。
"""

import unittest.mock as mock

from app.workers.skill_relation_propose import skill_relation_propose_daily


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


class TestGating:
    def test_disabled_by_default_skips(self):
        """开关默认 False → skipped，不触碰任何依赖（含 LLM 配置）。"""
        with mock.patch("app.core.runtime_config.get", return_value=False):
            result = asyncio_run(skill_relation_propose_daily({}))
        assert result["status"] == "skipped"
        assert "reason" in result

    def test_enabled_delegates_to_core(self):
        """开关开启 → 调用 propose 核心并透传 max_candidates。"""
        fake_summary = {"status": "ok", "candidates": 0, "proposed": 0}
        captured: dict = {}

        async def _fake_propose(limit):
            captured["limit"] = limit
            return fake_summary

        def _fake_get(key, default=None):
            if key == "skill_relation_propose_enabled":
                return True
            if key == "skill_relation_propose_max_candidates":
                return 66
            return default

        with mock.patch("app.core.runtime_config.get", side_effect=_fake_get), \
             mock.patch(
                 "app.services.llm_decision.propose_relations.propose",
                 new=_fake_propose,
             ):
            result = asyncio_run(skill_relation_propose_daily({}))
        assert result == fake_summary
        assert captured == {"limit": 66}


class TestShellCompat:
    def test_scripts_shell_reexports(self):
        """scripts 薄壳 re-export 核心符号（既有测试依赖 select_candidates）。"""
        from scripts.propose_skill_relations import (
            DEFAULT_LIMIT, MIN_COOCCUR, fetch_relation_inputs, select_candidates, propose,
        )
        assert MIN_COOCCUR == 2
        assert DEFAULT_LIMIT == 40
        assert callable(propose) and callable(select_candidates) and callable(fetch_relation_inputs)
