"""技能动态关系图同步纯函数测试（PR9b2：幂等 MERGE + 环拦截 + dry-run）。"""

from scripts.sync_dynamic_relations import apply_relations


class _FakeNeo4j:
    def __init__(self, known=("Java", "Spring")):
        self.queries: list = []
        self._known = known

    def run(self, query, **params):
        self.queries.append((query, params))
        if "WHERE x.name IN $names" in query:
            return [{"name": n} for n in self._known if n in params["names"]]
        return []


class TestSyncApply:
    def test_alternative_merged_bidirectional(self):
        fake = _FakeNeo4j()
        rows = [{"source_skill": "Java", "target_skill": "Spring", "relation_type": "ALTERNATIVE_OF"}]
        stats = apply_relations(rows, fake, {}, dry_run=False)
        assert stats["merged"] == 1
        merges = [q for q, _ in fake.queries if "MERGE" in q]
        assert len(merges) == 2  # 双向对称
        assert all("ALTERNATIVE_OF" in q for q in merges)

    def test_prerequisite_cycle_blocked(self):
        fake = _FakeNeo4j()
        rows = [{"source_skill": "Spring", "target_skill": "Java", "relation_type": "PREREQUISITE_OF"}]
        stats = apply_relations(rows, fake, {"Spring": {"Java"}}, dry_run=False)
        assert stats["cycle_blocked"] == 1
        assert stats["merged"] == 0

    def test_dry_run_no_write(self):
        fake = _FakeNeo4j()
        rows = [{"source_skill": "Java", "target_skill": "Spring", "relation_type": "PREREQUISITE_OF"}]
        stats = apply_relations(rows, fake, {}, dry_run=True)
        assert stats["merged"] == 1
        assert not any("MERGE" in q for q, _ in fake.queries)

    def test_missing_node_skipped(self):
        fake = _FakeNeo4j(known=("Java",))
        rows = [{"source_skill": "Java", "target_skill": "Spring", "relation_type": "BELONGS_TO"}]
        stats = apply_relations(rows, fake, {}, dry_run=False)
        assert stats["skipped_no_node"] == 1

    def test_self_reference_skipped(self):
        fake = _FakeNeo4j()
        rows = [{"source_skill": "Java", "target_skill": "Java", "relation_type": "BELONGS_TO"}]
        stats = apply_relations(rows, fake, {}, dry_run=False)
        assert stats["skipped_no_node"] == 1
