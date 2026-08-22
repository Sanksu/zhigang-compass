"""数据血缘溯源服务单元测试（P13 管理端可视化）。

覆盖 build_lineage（按归一化岗位名分组 + 组级跨源校验 + 证据 JD 血缘链明细）、
lineage_summary 总览统计，以及 admin lineage 路由的分页/过滤/详情/404。
"""

import asyncio

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.v1.admin_routes import lineage as lineage_router
from app.services.data_quality.lineage import build_lineage, lineage_summary
from app.services.data_quality.schemas import LineageDetail


def _rec(
    jd_id: int,
    source: str,
    position: str,
    skills: list[str],
    salary: str = "",
    location: str = "",
    duplicate_of: str | None = None,
) -> dict:
    snap = {
        "extraction": {
            "position_name": position,
            "skills": [{"name": s} for s in skills],
            "salary_range": salary,
        },
        "location": location,
    }
    if duplicate_of:
        snap["_duplicate_of"] = duplicate_of
    return {
        "id": jd_id,
        "source": source,
        "source_url": f"https://{source}.example/jd/{jd_id}",
        "crawled_at": "2026-08-02T00:00:00+08:00",
        "snapshot": snap,
    }


class TestBuildLineage:
    def test_group_and_chain(self):
        records = [
            _rec(1, "boss", "Python开发工程师", ["Python", "Django"], salary="22-28K", location="北京"),
            _rec(2, "zhilian", "python", ["Python", "Django", "Redis"], salary="20-25K", location="武汉"),
        ]
        details = build_lineage(records)
        assert len(details) == 1
        d = details[0]
        assert isinstance(d, LineageDetail)
        assert d.jd_count == 2
        assert d.source_count == 2
        assert d.verified is True
        # 血缘链明细按入库序，逐条可溯源到原始来源
        assert [r.jd_id for r in d.records] == [1, 2]
        assert d.records[0].source_url == "https://boss.example/jd/1"
        assert d.records[0].city == "北京"
        assert d.records[0].salary == "22-28K"

    def test_duplicate_flag_propagated(self):
        records = [
            _rec(1, "boss", "Java开发工程师", ["Java"], salary="22-28K", location="北京"),
            _rec(2, "zhilian", "java", ["Java"], salary="21-27K", location="北京", duplicate_of="1"),
        ]
        d = build_lineage(records)[0]
        assert d.records[1].is_duplicate is True
        assert d.records[0].is_duplicate is False

    def test_empty_records(self):
        assert build_lineage([]) == []


class TestLineageSummary:
    def _details(self):
        return build_lineage([
            _rec(1, "boss", "Python开发工程师", ["Python"], location="北京"),
            _rec(2, "zhilian", "python", ["Python"], location="北京"),
            _rec(3, "boss", "前端开发工程师", ["React"], location="武汉"),
        ])

    def test_summary_counts(self):
        s = lineage_summary(self._details())
        assert s["groups"] == 2
        assert s["jd_count"] == 3
        assert s["multi_source"] == 1  # 仅 Python 组 ≥2 源
        assert s["verified"] == 1


class _FakeDB:
    """AsyncSession 桩：捕获 stmt，返回预设行。"""

    def __init__(self, rows: list):
        self._rows = rows
        self.last_stmt = None

    async def scalars(self, stmt):
        self.last_stmt = stmt
        return self._rows


class _FakeRows:
    """scalars().all() 返回对象（模拟 SQLAlchemy ScalarResult）。"""

    def __init__(self, items: list):
        self._items = items

    def all(self):
        return self._items


class _FakeRow:
    """模拟 JDRaw ORM 行（仅取溯源所需字段）。"""

    def __init__(self, rec: dict):
        self.id = rec["id"]
        self.source = rec["source"]
        self.source_url = rec["source_url"]
        self.crawled_at = rec["crawled_at"]
        self.snapshot = rec["snapshot"]


class TestLineageRoutes:
    def _records(self):
        return [
            _FakeRow(_rec(1, "boss", "Python开发工程师", ["Python"], salary="22-28K", location="北京")),
            _FakeRow(_rec(2, "zhilian", "python", ["Python"], salary="20-25K", location="武汉")),
            _FakeRow(_rec(3, "boss", "前端开发工程师", ["React"], location="武汉")),
        ]

    def _make_db(self):
        return _FakeDB(_FakeRows(self._records()))

    def _run(self, coro):
        return asyncio.run(coro)

    def _list(self, db, **kwargs):
        """直调路由函数（FastAPI Query 默认值不经注入，需显式传参）。"""
        return self._run(
            lineage_router.lineage_positions(
                q=kwargs.get("q"),
                verified=kwargs.get("verified"),
                below_confidence=kwargs.get("below_confidence"),
                page=kwargs.get("page", 1),
                size=kwargs.get("size", 20),
                db=db,
            )
        )

    def test_list_pagination_and_summary(self):
        db = self._make_db()
        res = self._list(db)
        data = res.data
        assert data["total"] == 2
        assert len(data["items"]) == 2
        assert data["summary"]["groups"] == 2
        # 列表项不含血缘链明细（records 被排除）
        assert "records" not in data["items"][0]

    def test_filter_verified(self):
        db = self._make_db()
        res = self._list(db, verified=True)
        names = [i["position_name"] for i in res.data["items"]]
        assert names == ["Python开发工程师"]  # 仅 ≥2 源分组

    def test_filter_below_confidence(self):
        db = self._make_db()
        res = self._list(db, below_confidence=True)
        names = [i["position_name"] for i in res.data["items"]]
        assert names == ["前端开发工程师"]  # 单源低置信

    def test_filter_keyword(self):
        db = self._make_db()
        res = self._list(db, q="Python")
        names = [i["position_name"] for i in res.data["items"]]
        assert names == ["Python开发工程师"]

    def test_pagination_slice(self):
        db = self._make_db()
        res = self._list(db, page=2, size=1)
        assert len(res.data["items"]) == 1
        assert res.data["items"][0]["position_name"] == "前端开发工程师"

    def test_detail_returns_chain(self):
        db = self._make_db()
        res = self._run(lineage_router.lineage_position_detail("Python开发工程师", db))
        data = res.data
        assert data["jd_count"] == 2
        assert [r["jd_id"] for r in data["records"]] == [1, 2]

    def test_detail_404(self):
        db = self._make_db()
        try:
            self._run(lineage_router.lineage_position_detail("不存在的岗位", db))
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "status_code", None) == 404
            return
        raise AssertionError("岗位不存在应 404")

    def test_detail_route_matches_slash_position_name(self):
        """含 `/` 的岗位名详情须整段匹配（回归：前端显示「详情加载失败」）。"""
        row = _FakeRow(_rec(9, "boss", "AI/ML", ["Python"], location="北京"))
        row.snapshot = {**row.snapshot, "normalized_position": "AI/ML"}
        db = _FakeDB(_FakeRows([row]))

        app = FastAPI()
        app.include_router(lineage_router.router)

        async def _override_db():
            yield db

        app.dependency_overrides[lineage_router.get_db] = _override_db
        resp = TestClient(app).get("/lineage/positions/AI/ML")
        assert resp.status_code == 200
        assert resp.json()["data"]["position_name"] == "AI/ML"
