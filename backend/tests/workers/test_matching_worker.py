"""matching worker 编排层测试（第六轮审查 P1-1/P1-3 回归锁 + P1-6 覆盖洼地）。

workers/matching.py 此前覆盖率 10%（resume_parse/_match_jd_candidates/
match_recommend 编排全零测试）。本文件直调三个入口（fake session + mock 外设，
对齐 dict_guard 测试模式），锁定：
1. _load_jd_evidence_rows 每岗位独立 session（AsyncSession 禁止并发共用）；
2. 加载失败单岗位降级为空、其余岗位不受影响；
3. match_recommend PG 镜像失败 → rollback 后任务仍成功（PendingRollbackError
   不再逃逸、task.status 不卡 running）；
4. 匹配主流程异常 → task.status=failed 落终态。
"""

import asyncio
from types import SimpleNamespace

from app.workers import matching as mw


# ─────────────────────── P1-1：JD 证据独立 session ───────────────────────


class _RecordingSessionFactory:
    """并发发 session 的 fake 工厂：记录创建次数，验证不共用。"""

    def __init__(self):
        self.created = 0

    def __call__(self):
        self.created += 1
        return _PooledSession(self.created)


class _PooledSession:
    def __init__(self, sid: int):
        self.sid = sid

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalars(self, stmt):
        # 模拟真实 AsyncSession 行为：同一 session 上的并发请求在 gather 下
        # 只有串行才安全——此处按 sid 回数据，供断言各岗位拿到独立 session
        return SimpleNamespace(all=lambda: [])


class TestLoadJdEvidenceRows:
    def test_each_position_gets_its_own_session(self, monkeypatch):
        factory = _RecordingSessionFactory()
        monkeypatch.setattr("app.core.database.async_session_factory", factory)

        seen_sessions: list[int] = []

        async def _fake_load(session, name, limit=None):
            seen_sessions.append(session.sid)
            return [{"snapshot": {}, "source": "x", "source_url": ""}]

        monkeypatch.setattr(
            "app.services.matching.jd_rerank.load_jd_rows_for_position", _fake_load,
        )
        results = [
            SimpleNamespace(position_name="后端工程师"),
            SimpleNamespace(position_name="算法工程师"),
            SimpleNamespace(position_name="后端工程师"),  # 去重
        ]
        rows = asyncio.run(mw._load_jd_evidence_rows(results))

        assert set(rows) == {"后端工程师", "算法工程师"}
        assert len(seen_sessions) == 2
        # 每岗位独立 session（P1-1：共用同一 session 时 AsyncSession 并发禁用，
        # 除首个外全部抛错被吞 → jd_evidence 大面积静默为空）
        assert len(set(seen_sessions)) == 2

    def test_single_position_failure_degrades_to_empty(self, monkeypatch):
        monkeypatch.setattr(
            "app.core.database.async_session_factory", _RecordingSessionFactory(),
        )

        async def _fake_load(session, name, limit=None):
            if name == "坏岗位":
                raise RuntimeError("db down")
            return [{"snapshot": {"ok": True}}]

        monkeypatch.setattr(
            "app.services.matching.jd_rerank.load_jd_rows_for_position", _fake_load,
        )
        results = [
            SimpleNamespace(position_name="好岗位"),
            SimpleNamespace(position_name="坏岗位"),
        ]
        rows = asyncio.run(mw._load_jd_evidence_rows(results))
        assert rows == {"好岗位": [{"snapshot": {"ok": True}}], "坏岗位": []}

    def test_empty_names_short_circuit(self):
        assert asyncio.run(mw._load_jd_evidence_rows([])) == {}


# ─────────────────────── P1-3：match_recommend 编排 ───────────────────────


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


def _patch_match_env(monkeypatch, *, match_results=None, match_error=None):
    """mock 匹配外设：candidate 构建/画像缓存/匹配器/JD 证据/Redis。"""
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
        "app.services.matching.shared_cache.load_positions_shared", _noop_load,
    )
    monkeypatch.setattr(
        "app.services.matching.semantic.SkillEmbedder.get",
        classmethod(lambda cls: object()),
    )

    class _FakeMatcher:
        def __init__(self, positions, semantic=None):
            pass

        def match(self, request):
            if match_error is not None:
                raise match_error
            return match_results or []

    monkeypatch.setattr(
        "app.services.matching.engine.RuleBasedMatcher", _FakeMatcher,
    )
    monkeypatch.setattr(
        mw, "_load_jd_evidence_rows",
        lambda results: _noop_load(),
    )
    monkeypatch.setattr(
        "app.services.matching.jd_rerank.enrich_with_jd_evidence",
        lambda items, rows, skills: None,
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
            match_results=[
                SimpleNamespace(model_dump=lambda: {"position_name": "后端工程师", "score": 0.9}),
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
