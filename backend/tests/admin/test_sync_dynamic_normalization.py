"""名称归一落图同步纯函数测试（PR3 c：幂等 rename/merge + dry-run）。"""

from scripts.sync_dynamic_normalization import apply_normalizations


class _FakeNeo4j:
    """模拟 session.run：维护 known 名集合，记录 MERGE/RENAME/SET 调用与边合并。

    _exists 用 count() 查询（apply 内部通过 _exists 探测源/目标节点）。merge 会把
    source 移出 known、把 freq 累加到 target；rename 会把 source 名改为 target。
    这使「应用两次 → 第二次视为已完成（skipped_no_source）」的幂等语义可断言。
    """

    def __init__(self, entity_type="skill", known=("Java", "Javascript")):
        self._entity_type = entity_type
        self._known = dict.fromkeys(known, 1)  # name -> freq
        self.queries: list = []
        self.merge_calls = 0
        self.rename_calls = 0

    def run(self, query, **params):
        self.queries.append((query, params))
        # _exists：MATCH (n:Label {name:$name}) RETURN count(n) AS c → 需 .single()
        if "RETURN count(n) AS c" in query:
            return _Result([{"c": 1 if params.get("name") in self._known else 0}])
        # SET n.name = $target（rename）：把 source 移为 target
        if "SET n.name = $target" in query:
            src = params["source"]
            tgt = params["target"]
            if src in self._known:
                self._known[tgt] = self._known.pop(src)
                self.rename_calls += 1
            return _Result([])
        # DETACH DELETE d（merge 的收尾）：删除 source
        if "DETACH DELETE d" in query:
            src = params["source"]
            tgt = params["target"]
            if src in self._known:
                self._known[tgt] = self._known.get(tgt, 0) + self._known.pop(src)
                self.merge_calls += 1
            return _Result([])
        return _Result([])


class _Result:
    """伪装 neo4j Result：支持 .single()（取首条，无则 None）与迭代。"""

    def __init__(self, records):
        self._records = list(records)

    def single(self):
        return self._records[0] if self._records else None

    def __iter__(self):
        return iter(self._records)


def _row(entity_type="skill", action="merge", source="Javascript", target="Java"):
    return {"entity_type": entity_type, "action": action,
            "source_name": source, "target_name": target}


class TestSyncApply:
    def test_merge_moves_node_and_edges(self):
        fake = _FakeNeo4j(known=("Java", "Javascript"))
        stats = apply_normalizations([_row()], fake, dry_run=False)
        assert stats["merged"] == 1
        assert stats["skipped_no_source"] == 0
        assert fake.merge_calls == 1
        assert "Java" in fake._known
        assert "Javascript" not in fake._known

    def test_rename_when_target_missing(self):
        fake = _FakeNeo4j(known=("Javascript",))
        stats = apply_normalizations([_row(target="Java")], fake, dry_run=False)
        assert stats["renamed"] == 1
        assert fake.rename_calls == 1
        assert "Java" in fake._known
        assert "Javascript" not in fake._known

    def test_dry_run_no_write(self):
        fake = _FakeNeo4j(known=("Java", "Javascript"))
        stats = apply_normalizations([_row()], fake, dry_run=True)
        assert stats["merged"] == 1
        assert not any("DETACH DELETE" in q for q, _ in fake.queries)
        assert not any("SET n.name" in q for q, _ in fake.queries)

    def test_apply_twice_is_idempotent(self):
        """第一次 merge 后 source 消失，第二次视为已完成（skipped_no_source），结果一致。"""
        fake = _FakeNeo4j(known=("Java", "Javascript"))
        first = apply_normalizations([_row()], fake, dry_run=False)
        second = apply_normalizations([_row()], fake, dry_run=False)
        assert first["merged"] == 1
        assert second["skipped_no_source"] == 1
        assert second["merged"] == 0
        assert fake.merge_calls == 1  # 第二次不重复 merge

    def test_missing_source_skipped(self):
        fake = _FakeNeo4j(known=("Java",))
        stats = apply_normalizations([_row()], fake, dry_run=False)
        assert stats["skipped_no_source"] == 1
        assert stats["merged"] == 0

    def test_self_reference_skipped(self):
        fake = _FakeNeo4j(known=("Java",))
        stats = apply_normalizations([_row(source="Java", target="Java")], fake, dry_run=False)
        assert stats["skipped_no_source"] == 1

    def test_position_merge(self):
        fake = _FakeNeo4j(entity_type="position", known=("后端开发工程师", "后端工程师"))
        stats = apply_normalizations([_row(entity_type="position", source="后端工程师", target="后端开发工程师")],
                                    fake, dry_run=False)
        assert stats["merged"] == 1
        assert "后端开发工程师" in fake._known
