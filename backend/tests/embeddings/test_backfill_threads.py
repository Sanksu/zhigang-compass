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
    called = _spy_to_thread(monkeypatch)
    embedder = _FakeEmbedder()

    result = asyncio.run(backfill.backfill_skill_embeddings(_FakeDb(), embedder))

    assert result["written"] == 2
    assert backfill._fetch_skill_rows in called
    assert backfill._embed_all in called
    assert set(embedder.warmed) == {"Python", "Go"}


def test_jd_backfill_embeds_via_thread(monkeypatch):
    """jd 向量推理经 to_thread（不直接同步调用 embedder）。"""
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
