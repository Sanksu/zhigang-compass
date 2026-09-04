"""回归测试：discovery_daily 候选池 upsert 不覆盖已晋升岗位状态。

原实现无条件将 state 覆盖为 CANDIDATE，已晋升 emerging 的岗位次日被打回
candidate（状态机回退）。修复：仅仍为 candidate 的行允许状态覆盖。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.workers.discovery import _upsert_candidate


def _cand(name: str = "测试工程师", core_duties=None, typical_scenarios=None):
    return SimpleNamespace(
        candidate_id="cand-test",
        position_name=name,
        state=SimpleNamespace(value="candidate"),
        features=SimpleNamespace(model_dump=lambda: {"z_score": 1.0}),
        confidence=SimpleNamespace(model_dump=lambda: {"final_confidence": 0.7}),
        evidence_refs=[],
        seed_matched=False,
        rag_matched=False,
        definition_draft="",
        core_duties=core_duties or [],
        typical_scenarios=typical_scenarios or [],
        detected_at="2026-08-05T00:00:00+08:00",
    )


def _run(session, cand):
    asyncio.run(_upsert_candidate(session, cand))


def test_new_candidate_inserted_with_candidate_state():
    """无既有行时插入新候选（state=candidate）。"""
    session = SimpleNamespace(scalar=AsyncMock(return_value=None), add=Mock())
    _run(session, _cand())
    added = session.add.call_args[0][0]
    assert added.state == "candidate"


def test_structured_definition_persisted_when_present():
    """LLM 结构化定义（职责/场景）随 upsert 落库（赛题五字段，缺口1）。"""
    session = SimpleNamespace(scalar=AsyncMock(return_value=None), add=Mock())
    _run(session, _cand(
        core_duties=["设计 RAG 链路"],
        typical_scenarios=["企业知识库问答"],
    ))
    added = session.add.call_args[0][0]
    assert added.definition_structured == {
        "core_duties": ["设计 RAG 链路"],
        "typical_scenarios": ["企业知识库问答"],
    }


def test_empty_structured_definition_not_written_over_existing():
    """LLM 当日不可用（结构化为空）时不得用空对象抹掉历史草案。"""
    row = SimpleNamespace(
        state="candidate",
        definition_structured={"core_duties": ["历史职责"], "typical_scenarios": []},
    )
    session = SimpleNamespace(scalar=AsyncMock(return_value=row), add=Mock())
    _run(session, _cand())  # 本次无结构化产出
    assert row.definition_structured == {
        "core_duties": ["历史职责"],
        "typical_scenarios": [],
    }


def test_emerging_row_state_not_overwritten():
    """已晋升 emerging 的岗位不被 discovery_daily 打回 candidate（M15 核心）。"""
    row = SimpleNamespace(state="emerging")
    session = SimpleNamespace(scalar=AsyncMock(return_value=row), add=Mock())
    _run(session, _cand())
    assert row.state == "emerging"
    # 其余特征仍更新（仅 state 保留）
    assert row.features == {"z_score": 1.0}
    assert row.confidence == {"final_confidence": 0.7}


def test_stable_row_state_not_overwritten():
    row = SimpleNamespace(state="stable")
    session = SimpleNamespace(scalar=AsyncMock(return_value=row), add=Mock())
    _run(session, _cand())
    assert row.state == "stable"
