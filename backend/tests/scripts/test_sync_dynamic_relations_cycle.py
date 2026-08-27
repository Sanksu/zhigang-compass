"""sync_dynamic_relations 同批成环回归测试（第六轮审查 P2：批次内互逆先修）。

历史缺陷：apply_relations 逐条 MERGE 后不更新 prerequisite_map——同批
互逆 PREREQUISITE_OF（A→B、B→A）均通过守卫成环入图。锁定：
1. 同批互逆第二条被 cycle_blocked；
2. 落图后 map 更新（新边参与后续判定）；
3. merged_rows 携带 id/proposal_id（对账标记数据源）。
"""

from scripts.sync_dynamic_relations import apply_relations


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """仅响应节点存在性查询与 MERGE 写。"""

    def __init__(self, skills: set[str]):
        self.skills = skills
        self.merges: list[str] = []

    def run(self, query, **params):
        if "MERGE" in query:
            self.merges.append(query)
            return _FakeResult([])
        if "x.name IN $names" in query:
            names = set(params["names"])
            return _FakeResult([{"name": n} for n in names & self.skills])
        raise AssertionError(f"unexpected query: {query[:80]}")


def _row(source: str, target: str, rel: str = "PREREQUISITE_OF") -> dict:
    return {
        "source_skill": source, "target_skill": target, "relation_type": rel,
        "direction": "forward", "id": f"id_{source}_{target}".replace("→", "_"),
        "proposal_id": f"p_{source}_{target}".replace("→", "_"),
    }


class TestSameBatchCycleGuard:
    def test_inverse_pair_in_same_batch_second_blocked(self):
        session = _FakeSession({"A", "B"})
        rows = [_row("A", "B"), _row("B", "A")]
        stats = apply_relations(rows, session, prerequisite_map={})

        assert stats["merged"] == 1
        assert stats["cycle_blocked"] == 1  # 同批互逆第二条拦截（回归锁）
        assert len(session.merges) == 1

    def test_merged_row_updates_map_for_subsequent_rows(self):
        """非互逆链 A→B、B→C 合法；紧接 C→A 成环拦截（跨三条累积判定）。"""
        session = _FakeSession({"A", "B", "C"})
        rows = [_row("A", "B"), _row("B", "C"), _row("C", "A")]
        stats = apply_relations(rows, session, prerequisite_map={})

        assert stats["merged"] == 2
        assert stats["cycle_blocked"] == 1

    def test_merged_rows_carry_ids_for_reconciliation(self):
        session = _FakeSession({"A", "B"})
        stats = apply_relations([_row("A", "B")], session, prerequisite_map={})
        assert stats["merged_rows"] == [
            {"id": "id_A_B", "proposal_id": "p_A_B"}
        ]

    def test_existing_map_edges_block(self):
        """图内既有 B→A 时，批次内 A→B 拦截（既有 + 新边成环，原语义保持）。"""
        session = _FakeSession({"A", "B"})
        stats = apply_relations(
            [_row("A", "B")], session, prerequisite_map={"A": {"B"}},
        )
        assert stats["cycle_blocked"] == 1 and session.merges == []
