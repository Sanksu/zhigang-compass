"""matching worker 编排层测试（第六轮审查 P1-3 回归锁 + 方案 A 适配）。

workers/matching.py 现只保留 resume_parse / match_recommend 两入口；JD 候选评分
抽至 jd_match.score_jd_auto（recommend 恒走 JD 级，无聚合分支）。本文件直调
match_recommend（fake session + mock 外设，对齐 dict_guard 测试模式），锁定：
1. match_recommend PG 镜像失败 → rollback 后任务仍成功（PendingRollbackError
   不再逃逸、task.status 不卡 running）；
2. 匹配主流程异常 → task.status=failed 落终态；
3. 缺 task 返回 failed；幂等镜像落库复用已有行。
"""

import asyncio
from types import SimpleNamespace

from app.workers import matching as mw


class _FakeMirrorSession:
    """match_recommend 全流程 fake：get 返回 task/cache，scalar 可注入故障。"""

    def __init__(self, task, cache, *, scalar_error: Exception | None = None):
        self._task = task
        self._cache = cache
        self._scalar_error = scalar_error
        self.commits = 0
        self.rollbacks = 0
        self.added: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, model, pk):
        if model.__name__ == "TaskStatus":
            return self._task
        return self._cache

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def scalar(self, stmt):
        if self._scalar_error is not None:
            raise self._scalar_error
        return None

    async def flush(self):
        if self._scalar_error is not None:
            raise self._scalar_error


class _FakeRedis:
    def __init__(self):
        self.store: dict = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value


def _task_row() -> SimpleNamespace:
    return SimpleNamespace(
        id="t1", status="pending", progress=0.0, error=None, result=None,
    )


def _cache_row() -> SimpleNamespace:
    return SimpleNamespace(id="r1", file_hash="h", file_name="a.pdf",
                           parsed_data={"skills": [{"name": "Python"}]}, version=1)


def _patch_match_env(monkeypatch, *, data_items=None, match_error=None):
    """mock 匹配外设：候选构建/project 向量/score_jd_auto/Redis。"""
    async def _noop_load(*a, **kw):
        return {}

    from app.services.matching.schemas import CandidateProfile, CandidateSkill

    fake_candidate = CandidateProfile(
        user_id="u1",
        skills=[CandidateSkill(skill_id="sk_py", skill_name="Python", proficiency=2)],
    )
    monkeypatch.setattr(
        "app.services.matching.loaders.build_candidate",
        lambda parsed: fake_candidate,
    )
    monkeypatch.setattr(
        "app.services.embeddings.vector_store.load_project_vectors", _noop_load,
    )
    monkeypatch.setattr(
        "app.services.matching.semantic.SkillEmbedder.get",
        classmethod(lambda cls: object()),
    )

    async def _fake_scorer(session, candidate, project_vectors, top_n,
                           rough_k=None, semantic=None):
        if match_error is not None:
            raise match_error
        return data_items or []

    monkeypatch.setattr(
        "app.services.matching.jd_match.score_jd_auto", _fake_scorer,
    )
    fake_redis = _FakeRedis()
    monkeypatch.setattr("app.core.database.redis_client", fake_redis)
    return fake_redis


class TestMatchRecommendMirrorFailure:
    def test_mirror_flush_failure_rollbacks_and_task_succeeds(self, monkeypatch):
        """PG 镜像失败 → rollback + 任务仍成功（P1-3：无 rollback 时末尾
        commit 抛 PendingRollbackError 逃逸，task.status 卡 running）。"""
        fake_redis = _patch_match_env(
            monkeypatch,
            data_items=[
                {"position_name": "后端工程师", "score": 0.9},
            ],
        )
        task = _task_row()
        session = _FakeMirrorSession(
            task, _cache_row(), scalar_error=RuntimeError("mirror db down"),
        )
        monkeypatch.setattr("app.core.database.async_session_factory",
                            lambda: session)

        result = asyncio.run(mw.match_recommend(
            {}, resume_id="r1", top_n=10, task_id="t1", user_id="u1",
        ))

        assert result["status"] == "success"
        assert task.status == "success"
        assert session.rollbacks >= 1  # 镜像失败显式回滚（回归锁）
        assert session.commits >= 2    # 末尾统一 commit 未被逃逸异常吞掉
        assert f"match:result:{task.result['match_id']}" in fake_redis.store

    def test_match_exception_marks_task_failed(self, monkeypatch):
        """匹配主流程异常 → failed 落终态（含 error 文本）。"""
        _patch_match_env(monkeypatch, match_error=RuntimeError("engine boom"))
        task = _task_row()
        session = _FakeMirrorSession(task, _cache_row())
        monkeypatch.setattr("app.core.database.async_session_factory",
                            lambda: session)

        result = asyncio.run(mw.match_recommend(
            {}, resume_id="r1", top_n=10, task_id="t1", user_id="u1",
        ))

        assert result["status"] == "failed"
        assert task.status == "failed"
        assert "engine boom" in task.error

    def test_missing_task_returns_failed(self, monkeypatch):
        session = _FakeMirrorSession(None, _cache_row())
        monkeypatch.setattr("app.core.database.async_session_factory",
                            lambda: session)
        result = asyncio.run(mw.match_recommend(
            {}, resume_id="r1", top_n=10, task_id="ghost", user_id="u1",
        ))
        assert result == {"status": "failed", "error": "TaskStatus 不存在"}

    def test_deduplicated_position_name_in_mirror(self, monkeypatch):
        """镜像落库取 data_items 首项岗位名（JD 候选模式输出同构 dict 列表）。"""
        _patch_match_env(
            monkeypatch,
            data_items=[{"position_name": "算法工程师", "score": 0.7}],
        )
        task = _task_row()
        session = _FakeMirrorSession(task, _cache_row())
        monkeypatch.setattr("app.core.database.async_session_factory",
                            lambda: session)

        asyncio.run(mw.match_recommend(
            {}, resume_id="r1", top_n=10, task_id="t1", user_id="u1",
        ))
        mirror_rows = [obj for obj in session.added if obj.__class__.__name__ == "MatchResultRecord"]
        assert mirror_rows
        assert mirror_rows[0].position_name == "算法工程师"
