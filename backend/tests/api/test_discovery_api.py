"""新岗位发现路由测试（/discovery/recent + /discovery/position-skills-delta）。

不触真实 PG/Neo4j：fake AsyncSession 注入 DiscoveryCandidate/GraphVersion，
mock repository（回查技能）与 neo4j_driver。直调端点函数（对齐
evolution 测试模式），require_role 由 fake user 满足。
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.api.v1 import discovery as discovery_mod

_TZ = timezone(timedelta(hours=8))


class _FakeSession:
    """按查询返回行。scalars(...) 返回可迭代 scalar 结果。"""

    def __init__(self, rows):
        self._rows = rows

    async def scalars(self, stmt):
        return iter(self._rows)


def _candidate(position_name, state="candidate", detected=None):
    return SimpleNamespace(
        position_name=position_name,
        state=state,
        detected_at=detected or "2026-08-26T10:00:00+08:00",
        definition_draft=f"{position_name} 定义草案",
        confidence={"grounding": 0.8},
    )


def _version_skills(skills: list[tuple[str, str]]) -> dict:
    """建快照：岗位 p1 REQUIRES 指定技能。skills=[(skill_id, name)]。"""
    nodes = [{"id": "p1", "name": "后端工程师", "type": "position"}]
    edges = []
    for sid, name in skills:
        nodes.append({"id": sid, "name": name, "type": "skill"})
        edges.append({"source": "p1", "target": sid, "relation": "REQUIRES"})
    return {"nodes": nodes, "edges": edges}


def _version_row(vid, skills, created_days_ago=0):
    return SimpleNamespace(
        id=vid,
        created_at=datetime.now(_TZ) - timedelta(days=created_days_ago),
        snapshot_json=_version_skills(skills),
    )


class TestDiscoveryRecent:
    async def _run(self, monkeypatch, rows, *, skills_by_name=None):
        skills_by_name = skills_by_name or {}

        def _fake_query_by_name(driver, name):
            return skills_by_name.get(name, (None, {}))

        monkeypatch.setattr(
            discovery_mod.repository, "query_position_skills_by_name", _fake_query_by_name
        )
        return await discovery_mod.discovery_recent(
            days=30, state=None, limit=20, db=_FakeSession(rows),
            user={"role": "guest", "sub": "u1"},
        )

    @pytest.mark.asyncio
    async def test_returns_candidates_with_skills(self, monkeypatch):
        """有图谱技能的候选：skills 填充，skill_pending=False。"""
        rows = [_candidate("后端工程师", state="stable")]
        skills = {"must": [{"skill_id": "sk_1", "skill_name": "Python"}]}
        resp = await self._run(
            monkeypatch, rows, skills_by_name={"后端工程师": ("pos_1", skills)}
        )
        data = resp.data
        assert data["total"] == 1
        c = data["candidates"][0]
        assert c["position_id"] == "pos_1"
        assert c["position_name"] == "后端工程师"
        assert c["state"] == "stable"
        assert c["skills"]["must"][0]["skill_name"] == "Python"
        assert c["skill_pending"] is False

    @pytest.mark.asyncio
    async def test_candidate_without_graph_skills_marks_pending(self, monkeypatch):
        """图内无技能（candidate 未聚合）：skills=None + skill_pending=True。"""
        rows = [_candidate("量子运维工程师", state="candidate")]
        # 图谱无该岗位 → (None, {})
        resp = await self._run(monkeypatch, rows, skills_by_name={})
        c = resp.data["candidates"][0]
        assert c["position_id"] is None
        assert c["skills"] is None
        assert c["skill_pending"] is True

    @pytest.mark.asyncio
    async def test_empty_candidates(self, monkeypatch):
        """无候选：空列表 + total=0。"""
        resp = await self._run(monkeypatch, [])
        assert resp.data == {"candidates": [], "total": 0}


class TestPositionSkillsDelta:
    @pytest.mark.asyncio
    async def test_detects_added_removed_unchanged(self):
        """最近两版对比：新增 numpy、移除 Vue、未 Python。"""
        rows = [
            _version_row("v2", [("sk_py", "Python"), ("sk_np", "numpy")], created_days_ago=0),
            _version_row("v1", [("sk_py", "Python"), ("sk_vue", "Vue")], created_days_ago=1),
        ]
        resp = await discovery_mod.position_skills_delta(
            position="p1", db=_FakeSession(rows),
            user={"role": "guest", "sub": "u1"},
        )
        data = resp.data
        assert data["to_version"] == "v2"
        assert data["from_version"] == "v1"
        assert [s["skill_id"] for s in data["added"]] == ["sk_np"]
        assert [s["skill_id"] for s in data["removed"]] == ["sk_vue"]
        assert [s["skill_id"] for s in data["unchanged"]] == ["sk_py"]

    @pytest.mark.asyncio
    async def test_insufficient_snapshots_404(self):
        """快照不足 2 期：404。"""
        rows = [_version_row("v1", [])]
        resp = await discovery_mod.position_skills_delta(
            position="p1", db=_FakeSession(rows),
            user={"role": "guest", "sub": "u1"},
        )
        # error() 返回 JSONResponse，status_code=404
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_snapshots_no_crash(self):
        """空快照（个别期 edges 为空）不崩，技能集合差为空。"""
        empty_v1 = SimpleNamespace(
            id="v1", created_at=datetime.now(_TZ) - timedelta(days=1), snapshot_json={}
        )
        empty_v2 = SimpleNamespace(
            id="v2", created_at=datetime.now(_TZ), snapshot_json={}
        )
        resp = await discovery_mod.position_skills_delta(
            position="p1", db=_FakeSession([empty_v2, empty_v1]),
            user={"role": "guest", "sub": "u1"},
        )
        assert resp.data["added"] == [] and resp.data["removed"] == []
