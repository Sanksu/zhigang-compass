"""岗位人工编辑测试（设计文档 12.2：技能增删改 + 文本编辑 + PositionEditLog 日志）。

用 FakeTx 桩模拟 Neo4j 事务（参照 tests/kg/test_import_jd_nodes.py 模式），
覆盖：读详情返回解析、技能增删改的 Cypher 正确性、diff 摘要生成、
PositionEditLog 节点创建、参数校验失败。
"""

from app.api.v1.admin import (
    _edit_position_tx,
    _get_position_detail_tx,
    position_edit_diff,
    validate_position_edit,
)


class _Result:
    """查询结果桩：支持 .single() 与迭代（元素为 dict 行，模拟 Record 下标访问）。"""

    def __init__(self, rows):
        self._rows = rows

    def single(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _FakeTx:
    """事务桩：按查询关键字分发预置行，收集 run 调用；Counter MERGE 自增返回 seq。"""

    def __init__(self, rows_by_keyword=None):
        self.rows_by_keyword = rows_by_keyword or {}
        self.counters: dict[str, int] = {}
        self.queries = []

    def run(self, query, **params):
        self.queries.append((query, params))
        if "MERGE (c:Counter" in query:
            entity_type = params["entity_type"]
            seq = self.counters.get(entity_type, 0) + 1
            self.counters[entity_type] = seq
            return _Result([{"seq": seq}])
        for keyword, rows in self.rows_by_keyword.items():
            if keyword in query:
                return _Result(rows)
        return _Result([])


class _FakeSession:
    def __init__(self, tx):
        self._tx = tx

    def execute_read(self, fn, *args, **kwargs):
        return fn(self._tx, *args, **kwargs)

    def execute_write(self, fn, *args, **kwargs):
        return fn(self._tx, *args, **kwargs)


def _position_row(**overrides) -> dict:
    row = {
        "id": "pos_0001",
        "name": "数据分析师",
        "level": "中级",
        "industry": "互联网",
        "salary_range": "20-40K",
        "status": "emerging",
        "core_duties": ["数据报表"],
        "scenarios": ["数据中台"],
        "created_at": "2026-08-01T10:00:00+08:00",
        "updated_at": "2026-08-01T10:00:00+08:00",
    }
    row.update(overrides)
    return row


# ============================================================
# 参数校验
# ============================================================

class TestValidatePositionEdit:
    def test_valid_request_passes(self):
        skills = [
            {"name": "Python", "necessity": "must", "weight": 0.0},
            {"name": "SQL", "necessity": "nice", "weight": 1.0},
        ]
        assert validate_position_edit(skills, ["职责"], ["场景"]) is None
        assert validate_position_edit(None, None, None) is None

    def test_weight_out_of_range(self):
        for weight in (1.5, -0.1):
            msg = validate_position_edit(
                [{"name": "A", "necessity": "must", "weight": weight}], None, None
            )
            assert "weight" in msg

    def test_weight_non_numeric_or_missing(self):
        assert "weight" in validate_position_edit(
            [{"name": "A", "necessity": "must", "weight": "0.5"}], None, None
        )
        assert "weight" in validate_position_edit(
            [{"name": "A", "necessity": "must"}], None, None
        )
        assert "weight" in validate_position_edit(
            [{"name": "A", "necessity": "must", "weight": True}], None, None
        )

    def test_necessity_whitelist(self):
        assert "necessity" in validate_position_edit(
            [{"name": "A", "necessity": "required", "weight": 1.0}], None, None
        )

    def test_empty_skill_name(self):
        assert "name" in validate_position_edit(
            [{"name": "  ", "necessity": "must", "weight": 1.0}], None, None
        )

    def test_skills_shape(self):
        assert "skills" in validate_position_edit("不是数组", None, None)
        assert "skills" in validate_position_edit(["不是对象"], None, None)

    def test_text_fields_must_be_string_arrays(self):
        assert "core_duties" in validate_position_edit(None, "不是数组", None)
        assert "scenarios" in validate_position_edit(None, None, [1, 2])


# ============================================================
# diff 摘要
# ============================================================

class TestPositionEditDiff:
    def _current(self):
        return {
            "skills": [
                {"name": "A", "necessity": "must", "level": "", "weight": 1.0},
                {"name": "B", "necessity": "must", "level": "", "weight": 1.0},
                {"name": "C", "necessity": "must", "level": "", "weight": 1.0},
            ],
            "core_duties": ["x"],
            "scenarios": ["y"],
        }

    def test_added_updated_removed_combined(self):
        diff = position_edit_diff(
            self._current(),
            skills=[
                {"name": "A", "necessity": "must", "weight": 0.5},   # weight 变更
                {"name": "B", "necessity": "nice", "weight": 1.0},   # necessity 变更
                {"name": "D", "necessity": "must", "weight": 1.0},   # 新增
            ],
            core_duties=["x", "z"],
            scenarios=["y"],
        )
        assert diff == "skills +D, ~A/B, -C; core_duties 更新"

    def test_text_fields_only(self):
        diff = position_edit_diff(
            self._current(), None, core_duties=None, scenarios=["y", "z"]
        )
        assert diff == "scenarios 更新"

    def test_no_change_returns_empty(self):
        diff = position_edit_diff(
            self._current(),
            skills=[
                {"name": "A", "necessity": "must", "weight": 1.0},
                {"name": "B", "necessity": "must", "weight": 1.0},
                {"name": "C", "necessity": "must", "weight": 1.0},
            ],
            core_duties=["x"],
            scenarios=["y"],
        )
        assert diff == ""


# ============================================================
# 读岗位详情
# ============================================================

class TestGetPositionDetailTx:
    def test_parses_skills_education_certification(self):
        tx = _FakeTx({
            "RETURN p.id AS id": [_position_row()],
            "AS kind": [
                {"kind": "skill", "name": "Python", "necessity": "must", "weight": None, "level": "中级"},
                {"kind": "skill", "name": "SQL", "necessity": "nice", "weight": 0.6, "level": ""},
                {"kind": "education", "name": "本科", "necessity": "must", "weight": None, "level": ""},
                {"kind": "certification", "name": "PMP", "necessity": "nice", "weight": None, "level": ""},
            ],
        })
        detail = _get_position_detail_tx(tx, "数据分析师")
        assert detail["id"] == "pos_0001"
        assert detail["status"] == "emerging"
        assert detail["core_duties"] == ["数据报表"]
        assert detail["skills"] == [
            {"name": "Python", "necessity": "must", "level": "中级", "weight": 1.0},
            {"name": "SQL", "necessity": "nice", "level": "", "weight": 0.6},
        ]
        assert detail["education"] == [{"name": "本科", "necessity": "must", "level": ""}]
        assert detail["certifications"] == [{"name": "PMP", "necessity": "nice", "level": ""}]

    def test_zero_weight_preserved(self):
        """weight 0.0 是合法值，读端默认值不能覆盖它（不能用 or 兜底）。"""
        tx = _FakeTx({
            "RETURN p.id AS id": [_position_row(core_duties=None, scenarios=None)],
            "AS kind": [{"kind": "skill", "name": "S", "necessity": "must", "weight": 0.0, "level": ""}],
        })
        assert _get_position_detail_tx(tx, "数据分析师")["skills"][0]["weight"] == 0.0

    def test_position_not_found_returns_none(self):
        tx = _FakeTx()
        assert _get_position_detail_tx(tx, "不存在的岗位") is None


# ============================================================
# 编辑岗位定义
# ============================================================

class TestEditPositionTx:
    def test_skills_sync_and_edit_log_created(self):
        tx = _FakeTx({
            "RETURN p.id AS id": [_position_row()],
            "AS kind": [
                {"kind": "skill", "name": "Python", "necessity": "must", "weight": None, "level": ""},
                {"kind": "skill", "name": "Excel", "necessity": "must", "weight": None, "level": ""},
            ],
        })
        result = _edit_position_tx(
            tx, "数据分析师", "admin_01",
            skills=[
                {"name": "Python", "necessity": "must", "weight": 1.0},
                {"name": "SQL", "necessity": "nice", "weight": 0.6},
            ],
            core_duties=["数据报表", "可视化"],
            scenarios=None,
        )
        assert result == {
            "exists": True,
            "updated": True,
            "diff_summary": "skills +SQL, -Excel; core_duties 更新",
        }

        # 技能新增/更新：MERGE Skill 节点 + REQUIRES 关系并 SET necessity/weight
        merges = {
            params["skill_name"]: params
            for q, params in tx.queries if "MERGE (p)-[r:REQUIRES]->(sk)" in q
        }
        assert set(merges) == {"Python", "SQL"}
        assert merges["SQL"]["necessity"] == "nice"
        assert merges["SQL"]["weight"] == 0.6
        assert "MERGE (sk:Skill {name: $skill_name})" in [
            q for q, _ in tx.queries if "MERGE (p)-[r:REQUIRES]->(sk)" in q
        ][0]

        # 技能移除：仅删 REQUIRES 关系，不删 Skill 节点
        deletes = [params for q, params in tx.queries if "DELETE r" in q]
        assert len(deletes) == 1
        assert deletes[0]["skill_name"] == "Excel"
        assert "sk:Skill" in [q for q, _ in tx.queries if "DELETE r" in q][0]

        # 文本字段更新 Position 节点
        set_params = [p for q, p in tx.queries if "p.core_duties = $core_duties" in q]
        assert set_params and set_params[0]["core_duties"] == ["数据报表", "可视化"]
        assert "p.updated_at = $now" in [q for q, _ in tx.queries if "p.core_duties = $core_duties" in q][0]

        # PositionEditLog：审核员 ID + 时间戳 + diff 摘要
        logs = [p for q, p in tx.queries if "CREATE (l:PositionEditLog" in q]
        assert len(logs) == 1
        assert logs[0]["id"] == "pl_0001"
        assert logs[0]["position_name"] == "数据分析师"
        assert logs[0]["editor_id"] == "admin_01"
        assert logs[0]["diff_summary"] == "skills +SQL, -Excel; core_duties 更新"
        assert logs[0]["created_at"]

    def test_no_change_skips_all_writes(self):
        tx = _FakeTx({
            "RETURN p.id AS id": [_position_row()],
            "AS kind": [
                {"kind": "skill", "name": "Python", "necessity": "must", "weight": 1.0, "level": ""},
            ],
        })
        result = _edit_position_tx(
            tx, "数据分析师", "admin_01",
            skills=[{"name": "Python", "necessity": "must", "weight": 1.0}],
            core_duties=["数据报表"],
            scenarios=["数据中台"],
        )
        # id 供路由层失效岗位详情缓存（graph:position:{id}）
        assert result == {"exists": True, "updated": False, "diff_summary": "", "id": "pos_0001"}
        assert not any("PositionEditLog" in q for q, _ in tx.queries)
        assert not any("MERGE (p)-[r:REQUIRES]" in q for q, _ in tx.queries)
        assert not any("SET p." in q for q, _ in tx.queries)

    def test_position_not_found_writes_nothing(self):
        tx = _FakeTx()
        result = _edit_position_tx(
            tx, "不存在的岗位", "admin_01",
            skills=[{"name": "A", "necessity": "must", "weight": 1.0}],
            core_duties=None,
            scenarios=None,
        )
        assert result == {"exists": False, "updated": False, "diff_summary": ""}
        # 仅发过一次详情读查询，无任何写操作（MERGE/DELETE/CREATE）
        assert len(tx.queries) == 1
        assert all(not any(k in q for k in ("MERGE", "DELETE", "CREATE")) for q, _ in tx.queries)

    def test_empty_skills_list_removes_all(self):
        tx = _FakeTx({
            "RETURN p.id AS id": [_position_row()],
            "AS kind": [
                {"kind": "skill", "name": "Python", "necessity": "must", "weight": 1.0, "level": ""},
            ],
        })
        result = _edit_position_tx(
            tx, "数据分析师", "admin_01", skills=[], core_duties=None, scenarios=None
        )
        assert result["updated"] is True
        assert result["diff_summary"] == "skills -Python"
        deletes = [params for q, params in tx.queries if "DELETE r" in q]
        assert [d["skill_name"] for d in deletes] == ["Python"]
