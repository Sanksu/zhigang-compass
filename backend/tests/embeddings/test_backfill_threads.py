"""backfill 三表回填的线程池包装测试。

验证同步阻塞调用（Neo4j 读取 / SBERT 推理）经 asyncio.to_thread 执行，
防止改回直接同步调用导致 ARQ 事件循环阻塞（心跳超时）。
"""

import asyncio

from app.services.embeddings import backfill


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, *a, **k):
        return self

    def data(self):
        return [
            {"id": "sk-1", "name": "Python"},
            {"id": "sk-2", "name": "Go"},
        ]


class _FakeDriver:
    def session(self):
        return _FakeSession()


class _FakeEmbedder:
    def __init__(self):
        self.warmed = []

    def warm(self, names):
        self.warmed.extend(names)

    def embed(self, text):
        return [0.1] * 384


class _FakeDb:
    async def execute(self, stmt):
        return None

    async def commit(self):
        return None


def _spy_to_thread(monkeypatch):
    """包装 asyncio.to_thread，记录被包进的同步函数并原样执行。"""
    called: list = []

    async def _wrapped(fn, *args, **kwargs):
        called.append(fn)
        return fn(*args, **kwargs)

    monkeypatch.setattr(backfill.asyncio, "to_thread", _wrapped)
    return called


def test_skill_backfill_reads_and_embeds_via_thread(monkeypatch):
    """Neo4j 读取与 SBERT 推理均须经 to_thread。"""
    monkeypatch.setattr(backfill, "neo4j_driver", _FakeDriver())
    async def _no_existing(db):
        return {}
    monkeypatch.setattr(backfill, "_existing_skill_names", _no_existing)
    called = _spy_to_thread(monkeypatch)
    embedder = _FakeEmbedder()

    result = asyncio.run(backfill.backfill_skill_embeddings(_FakeDb(), embedder))

    assert result["written"] == 2
    assert backfill._fetch_skill_rows in called
    assert backfill._embed_all in called
    assert set(embedder.warmed) == {"Python", "Go"}


def test_jd_backfill_embeds_via_thread(monkeypatch):
    """jd 向量推理经 to_thread（不直接同步调用 embedder）。"""
    async def _no_existing(db):
        return {}
    monkeypatch.setattr(backfill, "_existing_jd_texts", _no_existing)
    called = _spy_to_thread(monkeypatch)
    embedder = _FakeEmbedder()

    class _Row:
        def __init__(self, rid, snapshot):
            self.id = rid
            self.snapshot = snapshot

    class _Db:
        def __init__(self):
            self.rows = [
                _Row(1, {"title": "后端工程师", "company": "X", "location": "北京"}),
                _Row(2, {"title": "算法工程师", "company": "Y", "location": "上海"}),
            ]

        async def scalars(self, stmt):
            return self

        def all(self):
            return self.rows

        async def execute(self, stmt):
            return None

        async def commit(self):
            return None

    result = asyncio.run(backfill.backfill_jd_embeddings(_Db(), embedder))

    assert result["written"] == 2
    assert backfill._embed_all in called


# ---------- 增量回填（08-23 闭环收敛 P1-2）：文本未变跳过推理 ----------


class _CountingEmbedder:
    def __init__(self):
        self.embedded: list[str] = []

    def warm(self, texts):
        pass

    def embed(self, text):
        self.embedded.append(text)
        return [0.1] * 384


def test_skill_backfill_skips_unchanged(monkeypatch):
    """已存向量且 name 一致 → 跳过推理，仅新技能重嵌。"""
    monkeypatch.setattr(backfill, "neo4j_driver", _FakeDriver())
    async def _existing(db):
        return {"sk-1": "Python"}  # sk-2 Go 未存
    monkeypatch.setattr(backfill, "_existing_skill_names", _existing)

    class _Db:
        async def execute(self, stmt):
            return None

        async def commit(self):
            return None

    embedder = _CountingEmbedder()
    result = asyncio.run(backfill.backfill_skill_embeddings(_Db(), embedder))

    assert result["written"] == 1
    assert result["skipped"] == 1
    assert embedder.embedded == ["Go"]


def test_jd_backfill_skips_unchanged(monkeypatch):
    """已存向量且 title+company+city 文本一致 → 跳过；变化 JD 重嵌。"""
    async def _existing(db):
        return {"1": "后端工程师 X 北京"}  # jd 2 未存
    monkeypatch.setattr(backfill, "_existing_jd_texts", _existing)

    class _Row:
        def __init__(self, rid, snapshot):
            self.id = rid
            self.snapshot = snapshot

    class _Db:
        def __init__(self):
            self.rows = [
                _Row(1, {"title": "后端工程师", "company": "X", "location": "北京"}),
                _Row(2, {"title": "算法工程师", "company": "Y", "location": "上海"}),
            ]

        async def scalars(self, stmt):
            return self

        def all(self):
            return self.rows

        async def execute(self, stmt):
            return None

        async def commit(self):
            return None

    embedder = _CountingEmbedder()
    result = asyncio.run(backfill.backfill_jd_embeddings(_Db(), embedder))

    assert result["written"] == 1
    assert result["skipped"] == 1
    assert embedder.embedded == ["算法工程师 Y 上海"]


def _project_db(resumes):
    class _Resume:
        def __init__(self, rid, parsed):
            self.id = rid
            self.parsed_data = parsed

    class _Db:
        def __init__(self):
            self.resumes = [_Resume(rid, parsed) for rid, parsed in resumes]
            self.executed: list = []
            self.added: list = []

        async def scalars(self, stmt):
            return self

        def all(self):
            return self.resumes

        async def execute(self, stmt):
            self.executed.append(stmt)
            return None

        async def commit(self):
            return None

        def add(self, obj):
            self.added.append(obj)

    return _Db()


def test_project_backfill_skips_unchanged_resume(monkeypatch):
    """简历项目集合与文本全部一致 → 整份跳过（无删除/无推理）。"""
    parsed = {"projects": [{"name": "推荐系统", "description": "协同过滤"}]}
    db = _project_db([("r1", parsed)])
    async def _existing(db_arg):
        return {("r1", 0): "推荐系统：协同过滤"}
    monkeypatch.setattr(backfill, "_existing_project_texts", _existing)

    embedder = _CountingEmbedder()
    result = asyncio.run(backfill.backfill_project_embeddings(db, embedder))

    assert result["written"] == 0
    assert result["skipped"] == 1
    assert embedder.embedded == []
    assert db.executed == []  # 无删除语句
    assert db.added == []


def test_project_backfill_rebuilds_changed_resume_only(monkeypatch):
    """变化简历重建（删旧插新），未变简历不动。"""
    db = _project_db([
        ("r1", {"projects": [{"name": "推荐系统", "description": "协同过滤"}]}),
        ("r2", {"projects": [{"name": "爬虫平台", "description": "Scrapy"}]}),
    ])
    async def _existing(db_arg):
        return {("r1", 0): "推荐系统：协同过滤"}  # r2 未存 → 变化
    monkeypatch.setattr(backfill, "_existing_project_texts", _existing)

    embedder = _CountingEmbedder()
    result = asyncio.run(backfill.backfill_project_embeddings(db, embedder))

    assert result["written"] == 1
    assert result["skipped"] == 1
    assert embedder.embedded == ["爬虫平台：Scrapy"]
    assert len(db.executed) == 1  # 仅 r2 的删除
    assert len(db.added) == 1
