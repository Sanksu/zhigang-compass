"""技能类别/软素质字段透出单元测试（软技能与技术栈技能区分展示）。

覆盖 queries.py 同步查询的结果组装（不依赖真实 Neo4j）：
- view/{view_type} 端点：skill 节点带 skill_category（软技能粉色渲染数据来源，
  见 tests/api/test_route_smoke.py 的 view 冒烟断言）
- query_position_skills_by_necessity / query_position_skills：技能项透传 skill_category
- load_position：PositionDetail.soft_skills 数据来源
- load_skill：SkillDetail.category 数据来源
"""

from app.services.graph import queries


class _Rec(dict):
    """支持 rec["k"] 取值与 rec.get("k", default) 的行。"""

    def get(self, key, default=None):
        return dict.get(self, key, default)


class _Node:
    """支持 .get(key, default) 的 Neo4j Node 桩。"""

    def __init__(self, **props):
        self._props = props

    def get(self, key, default=None):
        return self._props.get(key, default)


class _Result:
    """可迭代 + single() 的 run 返回值（load_position/load_skill 走 single 取行）。"""

    def __init__(self, rows):
        self._rows = list(rows)

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """按序返回预置行的同步 session 桩。"""

    def __init__(self, rows):
        self._rows = list(rows)

    def run(self, query, **params):
        return _Result([_Rec(r) if isinstance(r, dict) else r for r in self._rows])


def test_position_skills_by_necessity_passthrough():
    row = {
        "skill_id": "sk-1", "skill_name": "沟通能力", "necessity": "nice",
        "weight": 0.4, "level": "中级", "source_count": 2, "skill_category": "软技能",
    }
    skills = queries.query_position_skills_by_necessity(_FakeSession([row]), "pos-1")
    item = skills["nice"][0]
    assert item["skill_category"] == "软技能"
    assert item["skill_name"] == "沟通能力"


def test_position_skills_passthrough():
    row = {
        "skill_id": "sk-2", "skill_name": "Java", "necessity": "must",
        "weight": 0.8, "level": "高级", "source_count": 5, "skill_category": "编程语言",
    }
    items = queries.query_position_skills(_FakeSession([row]), "pos-1", None, "TRUE")
    assert items[0]["skill_category"] == "编程语言"


def test_load_position_returns_soft_skills():
    session = _FakeSession([{
        "id": "pos-1", "name": "后端工程师", "required_years": 3.0,
        "required_education": "本科", "last_updated": None, "status": "stable",
        "freq": 10, "soft_skills": ["沟通能力", "责任心"],
    }])
    position = queries.load_position(session, "pos-1")
    assert position["soft_skills"] == ["沟通能力", "责任心"]


def test_load_position_soft_skills_missing_defaults_none():
    session = _FakeSession([{
        "id": "pos-1", "name": "后端工程师", "required_years": None,
        "required_education": None, "last_updated": None, "status": "active",
        "freq": 5, "soft_skills": None,
    }])
    position = queries.load_position(session, "pos-1")
    assert position["soft_skills"] is None  # 路由层 `or []` 兜底


def test_load_skill_returns_category():
    session = _FakeSession([{"id": "sk-1", "name": "沟通能力", "category": "软技能"}])
    skill = queries.load_skill(session, "sk-1")
    assert skill["category"] == "软技能"
