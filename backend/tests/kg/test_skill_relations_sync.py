"""skill_relations 建边服务测试（第六轮审查 P1-6 覆盖洼地）。

services/kg/skill_relations.py 此前覆盖率 27%（九类关系落 Neo4j 的写入
路径无回归网，答辩图谱路径）。FakeNeo4j session 直测 sync_skill_relations
（三类关系建边方向/幂等 MERGE/缺节点跳过/dry-run）与 graph_prerequisite_chain
（拓扑序/环安全/未建边回退空）。
"""

from app.services.kg import skill_relations as sr


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows


class _FakeSession:
    """按查询形态分流：Skill 全量查询 / MERGE 写 / 先修链查询。"""

    def __init__(self, skills: set[str] | None = None, parents: dict[str, list[str]] | None = None):
        self.skills = skills or set()
        self.parents = parents or {}
        self.writes: list[tuple[str, dict]] = []

    def run(self, query, **params):
        if "MERGE" in query:
            self.writes.append((query, params))
            return _FakeResult([])
        if "MATCH (s:Skill) RETURN s.name" in query:
            return _FakeResult([{"name": n} for n in self.skills])
        if "PREREQUISITE_OF" in query and "RETURN p.name" in query:
            return _FakeResult([{"name": p} for p in self.parents.get(params.get("name", ""), [])])
        raise AssertionError(f"unexpected query: {query[:80]}")


def _patch_configs(monkeypatch, prereq: dict, relations: dict):
    def _fake_load(rel_path: str):
        if rel_path == sr._CONFIG_PREREQ:
            return {"skills": prereq}
        return {"skills": relations}

    monkeypatch.setattr(sr, "_load_yaml", _fake_load)


class TestSyncSkillRelations:
    def test_prerequisite_direction_pre_to_target(self, monkeypatch):
        _patch_configs(
            monkeypatch,
            prereq={"Java": {"prerequisites": ["C 语言"]}},
            relations={},
        )
        session = _FakeSession(skills={"Java", "C 语言"})
        stats = sr.sync_skill_relations(session)
        assert stats["prerequisite"] == 1 and stats["skipped"] == 0
        query, params = session.writes[0]
        assert "PREREQUISITE_OF" in query
        assert params == {"a": "C 语言", "b": "Java"}  # 先修 → 目标

    def test_belongs_to_child_to_parent_and_alternative_bidirectional(self, monkeypatch):
        _patch_configs(
            monkeypatch,
            prereq={},
            relations={
                "Spring Boot": {"parent": ["Java"], "alternatives": ["Spring"]},
            },
        )
        session = _FakeSession(skills={"Spring Boot", "Java", "Spring"})
        stats = sr.sync_skill_relations(session)
        assert stats["belongs_to"] == 1 and stats["alternative_of"] == 1  # 计数按对
        belongs = [(q, p) for q, p in session.writes if "BELONGS_TO" in q]
        assert belongs[0][1] == {"a": "Spring Boot", "b": "Java"}  # 子 → 父
        # ALTERNATIVE_OF 双向：两条 MERGE 同参数、方向在查询文本（(a)->(b) 与 (b)->(a)）
        alts = [q for q, _ in session.writes if "ALTERNATIVE_OF" in q]
        assert len(alts) == 2
        assert any("MERGE (a)-[:ALTERNATIVE_OF]->(b)" in q for q in alts)
        assert any("MERGE (b)-[:ALTERNATIVE_OF]->(a)" in q for q in alts)

    def test_missing_skill_nodes_skipped_no_stray_writes(self, monkeypatch):
        """字典条目技能不在图谱时跳过（不凭空建节点）。"""
        _patch_configs(
            monkeypatch,
            prereq={"Java": {"prerequisites": ["C 语言"]}},
            relations={"Kafka": {"parent": ["不存在技能"]}},
        )
        session = _FakeSession(skills={"Java"})  # C 语言 / Kafka / 父技能均不在图
        stats = sr.sync_skill_relations(session)
        assert stats["skipped"] >= 1 and session.writes == []

    def test_dry_run_counts_without_writes(self, monkeypatch):
        _patch_configs(monkeypatch, prereq={"Java": {"prerequisites": ["C 语言"]}}, relations={})
        session = _FakeSession(skills=set())  # dry_run 不查图谱
        stats = sr.sync_skill_relations(session, dry_run=True)
        assert stats["prerequisite"] == 1 and session.writes == []


class TestGraphPrerequisiteChain:
    def test_chain_in_topological_order(self):
        # Java ← C 语言 ← 计算机基础（基础 → C → Java）
        session = _FakeSession(parents={
            "Java": ["C 语言"],
            "C 语言": ["计算机基础"],
            "计算机基础": [],
        })
        chain = sr.graph_prerequisite_chain(session, "Java")
        assert chain == ["计算机基础", "C 语言"]  # 先修在前（拓扑序）

    def test_cycle_does_not_recurse_forever(self):
        session = _FakeSession(parents={"A": ["B"], "B": ["A"]})
        chain = sr.graph_prerequisite_chain(session, "A")
        assert chain == ["B"]  # 环安全：visited 截断不死循环，目标自身不入链

    def test_no_edges_returns_empty(self):
        session = _FakeSession(parents={})
        assert sr.graph_prerequisite_chain(session, "Java") == []
