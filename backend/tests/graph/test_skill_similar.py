"""graph.py skill_similar 端点单元测试（mock 数据，不依赖 Neo4j/Redis/真实 SBERT）。

覆盖 skill_similar（pgvector 主路径 → 异常降级 → 内存扫描回退）的完整分支：
- 技能不存在 404 / 缓存命中
- pgvector 主路径：Top-K、阈值过滤、top_k 截断、全低阈值空结果
- 降级回退：未回填（表空）、查询异常（表缺失/维度不匹配）
- 语义模型不可用：主路径与回退路径均返回 body code=503（error 默认 http 200 的既有契约）
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.v1 import graph as graph_api
from app.services.matching.semantic import SemanticUnavailableError

# 目标技能
SKILL = {"id": "s1", "name": "Python"}


# ---------- fake 依赖 ----------


class _FakeVec:
    """mock pgvector Vector：cosine_distance 返回预设距离。"""

    def __init__(self, distance: float):
        self._dist = distance

    def cosine_distance(self, _other) -> float:
        return self._dist


def _embedding_row(sid: str, name: str, distance: float) -> SimpleNamespace:
    """mock SkillEmbedding ORM 行：id/payload/embedding（含 cosine_distance）。"""
    return SimpleNamespace(id=sid, payload={"name": name}, embedding=_FakeVec(distance))


class _FakeEmbedder:
    """mock SkillEmbedder：embed/similarity 返回预设值或抛 SemanticUnavailableError。"""

    def __init__(self, sim_map: dict | None = None, *, raise_embed=False, raise_sim=False):
        self.sim_map = sim_map or {}
        self.raise_embed = raise_embed
        self.raise_sim = raise_sim
        self.embed_calls = 0
        self.similarity_calls = []

    def embed(self, text):
        self.embed_calls += 1
        if self.raise_embed:
            raise SemanticUnavailableError("模型不可用")
        return [0.1, 0.2]

    def similarity(self, a: str, b: str) -> float:
        self.similarity_calls.append((a, b))
        if self.raise_sim:
            raise SemanticUnavailableError("模型不可用")
        return self.sim_map.get(b, 0.0)


class _FakeScalarsResult:
    """mock db.scalars 的返回：.all() 返回行列表。"""

    def __init__(self, rows: list):
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _FakeDb:
    """mock AsyncSession：get 返回 target（None=未回填），scalars 返回 rows 或抛错。"""

    def __init__(self, *, target=None, rows=None, scalars_error=None):
        self._target = target
        self._rows = rows or []
        self._scalars_error = scalars_error
        self.get_calls = 0
        self.scalars_calls = 0

    async def get(self, _model, _id):
        self.get_calls += 1
        return self._target

    async def scalars(self, *_a, **_k):
        self.scalars_calls += 1
        if self._scalars_error is not None:
            raise self._scalars_error
        return _FakeScalarsResult(self._rows)


def _patch_redis(cached=None):
    """mock graph.redis_client：get 默认未命中（None），命中时返回 JSON 串。"""
    redis_mock = AsyncMock()
    redis_mock.get.return_value = cached
    return patch.object(graph_api, "redis_client", new=redis_mock)


def _fake_neo4j(skill_records: list[dict]) -> MagicMock:
    """mock graph.neo4j_driver：支持 with 上下文，内存扫描返回全量技能。

    内存扫描迭代 session.run(...) 的返回（Result），故 run 需可迭代。
    """
    run = MagicMock()
    run.__iter__.return_value = iter(skill_records)
    session = MagicMock()
    session.run.return_value = run
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    return driver


# ---------- 测试用例 ----------


def test_skill_not_found_404():
    """技能节点不存在 → 4040/404，不触碰后续依赖。"""
    db = _FakeDb()
    with patch.object(graph_api, "_load_skill", return_value=None), \
         _patch_redis():
        resp = asyncio.run(graph_api.skill_similar(skill_id="s404", top_k=10, db=db))
    assert resp.status_code == 404
    assert json.loads(resp.body)["code"] == 4040
    assert db.get_calls == 0


def test_cache_hit_skips_query():
    """Redis 命中 → 直接返回缓存数据，不查 pgvector / Neo4j。"""
    cached = {"skill_id": "s1", "skill_name": "Python", "similar": []}
    db = _FakeDb()
    with patch.object(graph_api, "_load_skill", return_value=SKILL), \
         _patch_redis(cached=json.dumps(cached)), \
         patch.object(graph_api.SkillEmbedder, "get") as embedder_mock:
        resp = asyncio.run(graph_api.skill_similar(skill_id="s1", top_k=10, db=db))
    assert resp.code == 0
    assert resp.data == cached
    assert db.get_calls == 0
    embedder_mock.assert_not_called()


def test_pgvector_main_path_topk_and_threshold():
    """主路径：余弦距离转相似度、阈值 0.5 过滤、按相似度降序返回。"""
    db = _FakeDb(
        target=_embedding_row("s1", "Python", 0.0),
        rows=[
            _embedding_row("s2", "Java", 0.3),      # 相似度 0.7
            _embedding_row("s3", "Go", 0.4),        # 相似度 0.6
            _embedding_row("s4", "Rust", 0.6),      # 相似度 0.4 → 低于阈值
        ],
    )
    embedder = _FakeEmbedder()
    with patch.object(graph_api, "_load_skill", return_value=SKILL), \
         _patch_redis(), \
         patch.object(graph_api.SkillEmbedder, "get", return_value=embedder):
        resp = asyncio.run(graph_api.skill_similar(skill_id="s1", top_k=10, db=db))

    assert resp.code == 0
    assert resp.data["skill_name"] == "Python"
    assert resp.data["similar"] == [
        {"skill_id": "s2", "skill_name": "Java", "similarity": 0.7},
        {"skill_id": "s3", "skill_name": "Go", "similarity": 0.6},
    ]
    # 主路径用 embed 绑定查询向量，不触发内存 similarity
    assert embedder.embed_calls == 1
    assert embedder.similarity_calls == []


def test_pgvector_topk_truncates():
    """主路径：候选数 > top_k 时按相似度截断。"""
    db = _FakeDb(
        target=_embedding_row("s1", "Python", 0.0),
        rows=[
            _embedding_row("s2", "Java", 0.1),
            _embedding_row("s3", "Go", 0.2),
            _embedding_row("s4", "Rust", 0.3),
        ],
    )
    with patch.object(graph_api, "_load_skill", return_value=SKILL), \
         _patch_redis(), \
         patch.object(graph_api.SkillEmbedder, "get", return_value=_FakeEmbedder()):
        resp = asyncio.run(graph_api.skill_similar(skill_id="s1", top_k=2, db=db))

    assert [s["skill_id"] for s in resp.data["similar"]] == ["s2", "s3"]


def test_pgvector_all_below_threshold_returns_empty():
    """主路径：全部候选低于阈值 0.5 → 空 similar 列表。"""
    db = _FakeDb(
        target=_embedding_row("s1", "Python", 0.0),
        rows=[_embedding_row("s2", "Java", 0.7)],
    )
    with patch.object(graph_api, "_load_skill", return_value=SKILL), \
         _patch_redis(), \
         patch.object(graph_api.SkillEmbedder, "get", return_value=_FakeEmbedder()):
        resp = asyncio.run(graph_api.skill_similar(skill_id="s1", top_k=10, db=db))

    assert resp.data["similar"] == []


def test_unfilled_falls_back_to_memory_scan():
    """未回填（skill_embeddings 无该技能）→ 降级内存 SBERT 全量扫描。"""
    db = _FakeDb(target=None)
    embedder = _FakeEmbedder(sim_map={"Java": 0.9, "Go": 0.4})
    with patch.object(graph_api, "_load_skill", return_value=SKILL), \
         _patch_redis(), \
         patch.object(graph_api.SkillEmbedder, "get", return_value=embedder), \
         patch.object(graph_api, "neo4j_driver",
                      _fake_neo4j([{"id": "s2", "name": "Java"},
                                   {"id": "s3", "name": "Go"}])):
        resp = asyncio.run(graph_api.skill_similar(skill_id="s1", top_k=10, db=db))

    assert resp.data["similar"] == [
        {"skill_id": "s2", "skill_name": "Java", "similarity": 0.9},
    ]
    # 降级路径不查 pgvector 行
    assert db.scalars_calls == 0


def test_pgvector_query_error_falls_back():
    """主路径查询异常（表缺失/维度不匹配）→ 降级内存扫描，不 500。"""
    db = _FakeDb(
        target=_embedding_row("s1", "Python", 0.0),
        scalars_error=Exception("relation skill_embeddings does not exist"),
    )
    embedder = _FakeEmbedder(sim_map={"Java": 0.8})
    with patch.object(graph_api, "_load_skill", return_value=SKILL), \
         _patch_redis(), \
         patch.object(graph_api.SkillEmbedder, "get", return_value=embedder), \
         patch.object(graph_api, "neo4j_driver",
                      _fake_neo4j([{"id": "s2", "name": "Java"}])):
        resp = asyncio.run(graph_api.skill_similar(skill_id="s1", top_k=10, db=db))

    assert resp.code == 0
    assert resp.data["similar"] == [
        {"skill_id": "s2", "skill_name": "Java", "similarity": 0.8},
    ]


def test_semantic_unavailable_on_main_path_returns_503():
    """主路径 embed 抛 SemanticUnavailableError → 503 语义不可用（不降级为猜）。"""
    db = _FakeDb(target=_embedding_row("s1", "Python", 0.0))
    with patch.object(graph_api, "_load_skill", return_value=SKILL), \
         _patch_redis(), \
         patch.object(graph_api.SkillEmbedder, "get",
                      return_value=_FakeEmbedder(raise_embed=True)):
        resp = asyncio.run(graph_api.skill_similar(skill_id="s1", top_k=10, db=db))

    # error(503, ...) 未显式传 http_status → 按 code 推导 HTTP 503，body code=503
    assert resp.status_code == 503
    assert json.loads(resp.body)["code"] == 503


def test_semantic_unavailable_on_fallback_returns_503():
    """内存扫描 similarity 抛 SemanticUnavailableError → 503。"""
    db = _FakeDb(target=None)
    with patch.object(graph_api, "_load_skill", return_value=SKILL), \
         _patch_redis(), \
         patch.object(graph_api.SkillEmbedder, "get",
                      return_value=_FakeEmbedder(raise_sim=True)), \
         patch.object(graph_api, "neo4j_driver",
                      _fake_neo4j([{"id": "s2", "name": "Java"}])):
        resp = asyncio.run(graph_api.skill_similar(skill_id="s1", top_k=10, db=db))

    # 与主路径一致：HTTP 503 + body code=503
    assert resp.status_code == 503
    assert json.loads(resp.body)["code"] == 503


def test_fallback_graph_empty_returns_empty():
    """图谱无任何 Skill 节点 → 空 similar 列表（不报错）。"""
    db = _FakeDb(target=None)
    with patch.object(graph_api, "_load_skill", return_value=SKILL), \
         _patch_redis(), \
         patch.object(graph_api.SkillEmbedder, "get", return_value=_FakeEmbedder()), \
         patch.object(graph_api, "neo4j_driver", _fake_neo4j([])):
        resp = asyncio.run(graph_api.skill_similar(skill_id="s1", top_k=10, db=db))

    assert resp.code == 0
    assert resp.data["similar"] == []
