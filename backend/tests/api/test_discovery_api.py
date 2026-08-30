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


class _FakeResult:
    """execute(...) 的返回：.all() 给行列表（对齐 ORM Row 行为，行带属性访问）。"""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """按查询返回行。scalars(...) 返回可迭代 scalar 结果；execute(...) 返回 _FakeResult。"""

    def __init__(self, rows):
        self._rows = rows

    async def scalars(self, stmt):
        # 每次调用返回新迭代器（同一 session 直调多跑端点时行可重复消费）
        return iter(self._rows)

    async def execute(self, stmt):
        return _FakeResult(self._rows)


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


def _version_with_education(skill_pairs, edu_pairs):
    """建含学历要求节点的快照：skill_pairs=(sid,name) 为 Skill，edu_pairs=(eid,name)
    为 Education 节点且同被 p1 REQUIRES——技能增减必须排除后者。"""
    snap = _version_skills(skill_pairs)
    for eid, name in edu_pairs:
        snap["nodes"].append({"id": eid, "name": name, "type": "Education"})
        snap["edges"].append({"source": "p1", "target": eid, "relation": "REQUIRES"})
    return snap


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
        # 图谱无该岗位 → (None, {})；candidate 不外泄：匿名与 guest 均过滤
        # （第七轮 P1-4：对齐 visibility 单一事实源，guest 原可越权看到待审核岗）
        def _fake_query_by_name(driver, name):
            return (None, {})

        monkeypatch.setattr(
            discovery_mod.repository, "query_position_skills_by_name", _fake_query_by_name
        )
        anon = await discovery_mod.discovery_recent(
            days=30, state=None, limit=20, db=_FakeSession(rows),
            user=None,
        )
        assert anon.data["candidates"] == []  # 匿名不外泄待审核岗位
        guest = await discovery_mod.discovery_recent(
            days=30, state=None, limit=20, db=_FakeSession(rows),
            user={"role": "guest", "sub": "u1"},
        )
        assert guest.data["candidates"] == []  # guest 同样不外泄（P1-4 修复）
        # user 角色及以上可见；该 candidate 图内无技能 → 标注待审核
        user = await discovery_mod.discovery_recent(
            days=30, state=None, limit=20, db=_FakeSession(rows),
            user={"role": "user", "sub": "u1"},
        )
        c = user.data["candidates"][0]
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
            position="p1", from_version=None, to_version=None,
            db=_FakeSession(rows),
            user={"role": "guest", "sub": "u1"},
        )
        data = resp.data
        assert data["to_version"] == "v2"
        assert data["from_version"] == "v1"
        assert [s["skill_id"] for s in data["added"]] == ["sk_np"]
        assert [s["skill_id"] for s in data["removed"]] == ["sk_vue"]
        assert [s["skill_id"] for s in data["unchanged"]] == ["sk_py"]

    @pytest.mark.asyncio
    async def test_explicit_version_pair(self):
        """显式 from/to：跨期对比 v1 → v3（跳过 v2）。"""
        rows = [
            _version_row("v3", [("sk_py", "Python"), ("sk_gr", "GraphQL")], created_days_ago=0),
            _version_row("v2", [("sk_py", "Python"), ("sk_np", "numpy")], created_days_ago=1),
            _version_row("v1", [("sk_py", "Python"), ("sk_vue", "Vue")], created_days_ago=2),
        ]
        resp = await discovery_mod.position_skills_delta(
            position="p1", from_version="v1", to_version="v3", db=_FakeSession(rows),
            user={"role": "guest", "sub": "u1"},
        )
        data = resp.data
        assert (data["from_version"], data["to_version"]) == ("v1", "v3")
        assert [s["skill_id"] for s in data["added"]] == ["sk_gr"]
        assert [s["skill_id"] for s in data["removed"]] == ["sk_vue"]

    @pytest.mark.asyncio
    async def test_same_version_pair_404(self):
        """from == to：404 拒绝。"""
        rows = [
            _version_row("v2", [], created_days_ago=0),
            _version_row("v1", [], created_days_ago=1),
        ]
        resp = await discovery_mod.position_skills_delta(
            position="p1", from_version="v1", to_version="v1",
            db=_FakeSession(rows),
            user={"role": "guest", "sub": "u1"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_version_404(self):
        """指定不存在的版本：404。"""
        rows = [
            _version_row("v2", [], created_days_ago=0),
            _version_row("v1", [], created_days_ago=1),
        ]
        resp = await discovery_mod.position_skills_delta(
            position="p1", from_version="v99", to_version=None,
            db=_FakeSession(rows),
            user={"role": "guest", "sub": "u1"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_insufficient_snapshots_404(self):
        """快照不足 2 期：404。"""
        rows = [_version_row("v1", [])]
        resp = await discovery_mod.position_skills_delta(
            position="p1", from_version=None, to_version=None,
            db=_FakeSession(rows),
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
            position="p1", from_version=None, to_version=None,
            db=_FakeSession([empty_v2, empty_v1]),
            user={"role": "guest", "sub": "u1"},
        )
        assert resp.data["added"] == [] and resp.data["removed"] == []


class TestEducationExcluded:
    """REQUIRES 的 Education target（学历要求）不算技能——226 实证
    「本科 · 计算机科学」等 ed_* 节点曾混入技能增减列表。"""

    @pytest.mark.asyncio
    async def test_detail_excludes_education(self):
        rows = [
            _version_row("v2", [("sk_py", "Python")], created_days_ago=0),
            _version_row("v1", [], created_days_ago=1),
        ]
        # v2 额外带学历要求节点
        rows[0].snapshot_json = _version_with_education(
            [("sk_py", "Python")], [("ed_1", "本科 · 计算机科学"), ("ed_2", "大专")]
        )
        resp = await discovery_mod.position_skills_delta(
            position="p1", from_version=None, to_version=None,
            db=_FakeSession(rows),
            user={"role": "guest", "sub": "u1"},
        )
        data = resp.data
        assert [s["skill_id"] for s in data["added"]] == ["sk_py"]
        assert all(not s["skill_id"].startswith("ed_") for s in data["added"])

    @pytest.mark.asyncio
    async def test_summary_excludes_education(self):
        rows = [
            _version_row("v2", [("sk_py", "Python")], created_days_ago=0),
            _version_row("v1", [("sk_py", "Python")], created_days_ago=1),
        ]
        for r in rows:
            r.snapshot_json = _version_with_education(
                [("sk_py", "Python")], [("ed_1", "本科")]
            )
        resp = await discovery_mod.position_skills_delta_summary(
            from_version=None, to_version=None,
            db=_FakeSession(rows),
            user={"role": "guest", "sub": "u1"},
        )
        p = resp.data["positions"][0]
        # 学历边两版都有且不计入：未变仅 sk_py
        assert p["added"] == 0 and p["removed"] == 0 and p["unchanged"] == 1


class TestPositionSkillsDeltaSummary:
    def _two_position_rows(self):
        """v1：p1(Java) / p2(Go)；v2：p1(Java+K8s) / p2(Go)——p1 有增减、p2 稳定。"""

        def snap(entries):
            nodes, edges = [], []
            for pid, pname, sid, sname in entries:
                nodes.append({"id": pid, "name": pname, "type": "position"})
                nodes.append({"id": sid, "name": sname, "type": "skill"})
                edges.append({"source": pid, "target": sid, "relation": "REQUIRES"})
            return {"nodes": nodes, "edges": edges}

        v1 = SimpleNamespace(
            id="v1", created_at=datetime.now(_TZ) - timedelta(days=1),
            snapshot_json=snap([
                ("p1", "后端工程师", "sk_java", "Java"),
                ("p2", "Go 工程师", "sk_go", "Go"),
            ]),
        )
        v2 = SimpleNamespace(
            id="v2", created_at=datetime.now(_TZ),
            snapshot_json=snap([
                ("p1", "后端工程师", "sk_java", "Java"),
                ("p1", "后端工程师", "sk_k8s", "Kubernetes"),
                ("p2", "Go 工程师", "sk_go", "Go"),
            ]),
        )
        return [v2, v1]

    @pytest.mark.asyncio
    async def test_summary_counts_and_stable(self):
        """汇总：p1 新增 1（有增减）、p2 全稳（0 增减）；versions 全量返回。"""
        resp = await discovery_mod.position_skills_delta_summary(
            from_version=None, to_version=None,
            db=_FakeSession(self._two_position_rows()),
            user={"role": "guest", "sub": "u1"},
        )
        data = resp.data
        assert (data["from_version"], data["to_version"]) == ("v1", "v2")
        assert [v["id"] for v in data["versions"]] == ["v2", "v1"]
        by_name = {p["position_name"]: p for p in data["positions"]}
        assert by_name["后端工程师"]["added"] == 1
        assert by_name["后端工程师"]["removed"] == 0
        assert by_name["后端工程师"]["unchanged"] == 1
        assert by_name["Go 工程师"]["added"] == 0
        assert by_name["Go 工程师"]["unchanged"] == 1
        # 排序：有增减的 p1 在稳定 p2 之前
        assert data["positions"][0]["position_name"] == "后端工程师"

    @pytest.mark.asyncio
    async def test_summary_explicit_pair(self):
        """显式版本对生效。"""
        resp = await discovery_mod.position_skills_delta_summary(
            from_version="v1", to_version="v2",
            db=_FakeSession(self._two_position_rows()),
            user={"role": "guest", "sub": "u1"},
        )
        assert resp.data["from_version"] == "v1"

    @pytest.mark.asyncio
    async def test_summary_insufficient_snapshots_404(self):
        """快照不足 2 期：404。"""
        resp = await discovery_mod.position_skills_delta_summary(
            db=_FakeSession([_version_row("v1", [])]),
            user={"role": "guest", "sub": "u1"},
        )
        assert resp.status_code == 404
