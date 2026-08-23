"""ETL 增量化任务测试（08-23 闭环收敛 P1-1）。

- sync_skill_normalization：输入指纹一致整体跳过（不加载 SBERT）；force 绕过
- load_courses：Course.import_hash 一致跳过重导；变化课程重导并更新指纹
- evaluate_courses：六维输入指纹一致复用已存评分（零写放大）；变化重评
"""

import asyncio
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))

import pytest

from app.services.extraction.normalization import DISTANCE_THRESHOLD, input_fingerprint


# ---------- 纯函数：指纹确定性与敏感性 ----------


def test_fingerprint_deterministic_and_sensitive():
    alias = {"Go": "Golang"}
    fp = input_fingerprint(["Python", "机器学习"], alias, DISTANCE_THRESHOLD)
    assert fp == input_fingerprint(["机器学习", "Python"], alias, DISTANCE_THRESHOLD)  # 序无关
    assert fp != input_fingerprint(["Python", "机器学习", "新技能"], alias, DISTANCE_THRESHOLD)
    assert fp != input_fingerprint(["Python", "机器学习"], {"Go": "Golang", "K8s": "Kubernetes"}, DISTANCE_THRESHOLD)
    assert fp != input_fingerprint(["Python", "机器学习"], alias, DISTANCE_THRESHOLD + 0.01)


# ---------- sync_skill_normalization：指纹跳过 ----------


class _NormResult:
    """session.run(query) 的结果桩：按查询文本分发 data()/single()。"""

    def __init__(self, rows=None, single=None):
        self._rows = rows or []
        self._single = single

    def data(self):
        return self._rows

    def single(self):
        return self._single


class _NormSession:
    def __init__(self, skill_rows, state_single):
        self._skill_rows = skill_rows
        self._state = state_single
        self.runs: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, query, **params):
        self.runs.append(query)
        if "SkillNormState" in query and "RETURN" in query:
            return _NormResult(single=self._state)
        if "RETURN s.name" in query:
            return _NormResult(rows=self._skill_rows)
        return _NormResult()


class _NormDriver:
    def __init__(self, skill_rows, state_single):
        self._session = _NormSession(skill_rows, state_single)

    def session(self):
        return self._session


def _patch_norm_env(monkeypatch, state_single):
    import app.core.database as database_mod
    import app.services.extraction.normalization as norm_mod
    from app.workers import etl_tasks

    monkeypatch.setattr(
        database_mod, "neo4j_driver", _NormDriver(
            [{"name": "Python"}, {"name": "机器学习"}], state_single
        )
    )
    monkeypatch.setattr(norm_mod, "_default_alias", lambda: {"Go": "Golang"})
    # SkillNormalizer 构造即加载 SBERT（_run 内延迟 import，patch 源模块）——
    # 被触达即说明未走跳过路径
    def _forbidden(*a, **k):
        raise AssertionError("SkillNormalizer 不应被构造（指纹跳过失效）")
    monkeypatch.setattr(norm_mod, "SkillNormalizer", _forbidden)
    return etl_tasks


def test_skill_norm_skips_on_matching_fingerprint(monkeypatch):
    """指纹一致 → 直接返回上次 summary + skipped 标记，不加载 SBERT。"""
    names = ["Python", "机器学习"]
    fp = input_fingerprint(names, {"Go": "Golang"}, DISTANCE_THRESHOLD)
    etl_tasks = _patch_norm_env(
        monkeypatch,
        {"fp": fp, "summary": {"skills": 2, "normalized": 5, "similar_pairs": 9,
                               "skipped_standard": 0, "detail": "SIMILAR_TO 已回写（幂等）"}},
    )

    result = asyncio.run(etl_tasks.sync_skill_normalization({}))

    assert result["skipped"] == "input_unchanged"
    assert result["skills"] == 2 and result["similar_pairs"] == 9


def test_skill_norm_fingerprint_mismatch_proceeds(monkeypatch):
    """指纹不一致（技能集变化）→ 进入全量路径（此处以构造被触达为证）。"""
    etl_tasks = _patch_norm_env(monkeypatch, {"fp": "stale", "summary": {"skills": 1}})

    with pytest.raises(AssertionError, match="不应被构造"):
        asyncio.run(etl_tasks.sync_skill_normalization({}))


def test_skill_norm_force_bypasses_skip(monkeypatch):
    """force=True → 即使指纹一致也全量重算。"""
    names = ["Python", "机器学习"]
    fp = input_fingerprint(names, {"Go": "Golang"}, DISTANCE_THRESHOLD)
    etl_tasks = _patch_norm_env(monkeypatch, {"fp": fp, "summary": {"skills": 2}})

    with pytest.raises(AssertionError, match="不应被构造"):
        asyncio.run(etl_tasks.sync_skill_normalization({}, force=True))


# ---------- load_courses：import_hash 跳过 ----------


class _CourseSession:
    def __init__(self, results):
        self._results = results  # 跨 run 持久（挂在 driver 上）：模拟 Neo4j 库内状态
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, query, **params):
        self.queries.append(query)
        if "RETURN c.source" in query:
            return _NormResult(rows=[
                {"s": "coursera", "sid": "c1", "h": self._results.get("c1")},
                {"s": "coursera", "sid": "c2", "h": self._results.get("c2")},
            ])
        if "SET c.import_hash" in query:
            self._results[params["sid"]] = params["fp"]
        return _NormResult()


class _CourseDriver:
    def __init__(self):
        self.results: dict[str, str] = {}  # (source_id → import_hash) 库内持久状态

    def session(self):
        return _CourseSession(self.results)


def test_load_courses_skips_unchanged(monkeypatch):
    """指纹一致的课程跳过 import_course；变化课程重导 + 更新指纹。"""
    import app.core.database as database_mod
    from app.workers import courses as courses_mod

    driver = _CourseDriver()
    monkeypatch.setattr(database_mod, "neo4j_driver", driver)

    snap1 = {"source": "coursera", "source_id": "c1", "title": "Python 入门", "skills": ["Python"]}
    snap2 = {"source": "coursera", "source_id": "c2", "title": "机器学习", "skills": ["机器学习"]}
    rows = [type("R", (), {"snapshot": snap1})(), type("R", (), {"snapshot": snap2})()]

    class _FakeAsyncSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def scalars(self, stmt):
            return self

        def all(self):
            return rows

    monkeypatch.setattr(database_mod, "async_session_factory", lambda: _FakeAsyncSession())

    imported_ids: list[str] = []

    import app.services.kg.kg_service as kg_service
    def _fake_import(session, course_data):
        imported_ids.append(course_data["source_id"])
        return "co_x"

    monkeypatch.setattr(kg_service, "import_course", _fake_import)

    result = asyncio.run(courses_mod.load_courses({}))
    assert imported_ids == ["c1", "c2"] and result["skipped"] == 0

    # 第二轮：指纹已存 → 全部跳过
    imported_ids.clear()
    result = asyncio.run(courses_mod.load_courses({}))
    assert imported_ids == []
    assert result["skipped"] == 2 and result["imported"] == 0


# ---------- evaluate_courses：输入指纹复用 ----------


def test_evaluate_courses_reuses_unchanged_quality(monkeypatch):
    """输入未变 → 复用已存评分（不回写 snapshot）；输入变化 → 重评 + 落指纹。"""
    import app.core.database as database_mod
    from app.services.data_quality import course_quality as cq_mod
    from app.services.data_quality.schemas import CourseQualityResult
    from app.workers import courses as courses_mod

    unchanged_snap = {
        "title": "Python 入门", "platform": "coursera", "rating": 4.8,
        "enrollment": 1000, "start_date": "2026-01-01",
        "skills": ["Python"], "description": "入门课",
    }
    stored_quality = {
        "title": "Python 入门", "platform": "coursera",
        "platform_score": 1.0, "rating_score": 0.9, "enrollment_score": 0.8,
        "recency_score": 0.7, "skill_coverage_score": 0.6,
        "project_density_score": 0.5, "quality_score": 0.9,
        "recommended": True,
        "input_hash": courses_mod._fingerprint(unchanged_snap, courses_mod._EVAL_FIELDS),
    }
    snap1 = dict(unchanged_snap, quality=dict(stored_quality))
    snap2 = {"title": "机器学习", "platform": "edx", "rating": 4.5,
             "enrollment": 500, "skills": ["机器学习"], "description": "ML"}
    rows = [type("R", (), {"snapshot": snap1})(), type("R", (), {"snapshot": snap2})()]

    class _FakeAsyncSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def scalars(self, stmt):
            return self

        def all(self):
            return rows

        async def commit(self):
            pass

    monkeypatch.setattr(database_mod, "async_session_factory", _FakeAsyncSession)

    eval_calls: list[str] = []

    def _fake_evaluate(snap):
        eval_calls.append(snap["title"])
        return CourseQualityResult(
            title=snap.get("title", ""), platform=snap.get("platform", ""),
            platform_score=0.5, rating_score=0.5, enrollment_score=0.5,
            recency_score=0.5, skill_coverage_score=0.5,
            project_density_score=0.5, quality_score=0.5, recommended=False,
        )

    monkeypatch.setattr(cq_mod, "evaluate_course", _fake_evaluate)

    result = asyncio.run(courses_mod.evaluate_courses({}))

    # 未变课程走复用（evaluate_course 未被调用），变化课程重评一次
    assert eval_calls == ["机器学习"]
    # 未变课程 quality 原样保留（未被改写）
    assert rows[0].snapshot["quality"] == stored_quality
    # 变化课程 quality 带新指纹
    assert rows[1].snapshot["quality"]["input_hash"]
    assert result["total"] == 2 and result["recommended"] == 1
