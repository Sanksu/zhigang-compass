"""RAG 接地单元测试（设计文档 7.2.3 节）。

覆盖种子列表匹配与权威库检索的匹配口径。
"""

import asyncio

import pytest

from app.services.discovery.grounding import (
    _generate_definition,
    _merge_hits,
    match_seed,
    search_authoritative,
    sanitize_fulltext,
)


@pytest.fixture(autouse=True)
def _disable_grounding_cache(monkeypatch):
    """单测不依赖 Redis：关闭 grounding 检索缓存，避免命中真实缓存污染断言。"""
    from app.services.discovery import grounding

    monkeypatch.setattr(grounding, "_CACHE_ENABLED", False)

_SEEDS = [
    {
        "name": "RAG 工程师",
        "aliases": ["检索增强生成工程师", "RAG Developer"],
        "description": "专注检索增强生成系统的构建与优化",
    },
    {
        "name": "AI Agent 工程师",
        "aliases": ["Agent 开发", "智能体工程师"],
        "description": "负责基于大语言模型的智能体应用设计与开发",
    },
]


class TestMatchSeed:
    def test_exact_name_match(self):
        seed = match_seed("RAG 工程师", _SEEDS)
        assert seed is not None
        assert seed["name"] == "RAG 工程师"

    def test_alias_match(self):
        seed = match_seed("检索增强生成工程师", _SEEDS)
        assert seed is not None
        assert seed["name"] == "RAG 工程师"

    def test_reverse_substring(self):
        """岗位名含种子名（如 'RAG 工程师（资深）' 命中 'RAG 工程师'）。"""
        seed = match_seed("RAG 工程师（资深）", _SEEDS)
        assert seed is not None

    def test_partial_contained(self):
        """种子名含岗位名（如 'Agent 开发' 命中 'Agent 开发工程师'）。"""
        seed = match_seed("Agent 开发工程师", _SEEDS)
        assert seed is not None
        assert seed["name"] == "AI Agent 工程师"

    def test_no_match(self):
        assert match_seed("焊工", _SEEDS) is None

    def test_empty_position(self):
        assert match_seed("", _SEEDS) is None

    def test_empty_seeds(self):
        assert match_seed("RAG 工程师", []) is None


class TestSearchAuthoritative:
    @pytest.fixture(autouse=True)
    def _event_loop(self):
        """仅测纯函数路径：本类用例通过 fake db 覆盖 SQL 组装逻辑可暂不执行。"""
        yield

    def test_sql_builds_with_invalid_chars(self):
        """含 %/_ 通配符的岗位名不抛 SQL 异常（参数化，非注入）。"""
        async def _run():

            class _FakeDb:
                async def scalars(self, stmt):
                    # 仅校验 stmt 可编译（参数化查询），返回空
                    import sqlalchemy
                    try:
                        sqlalchemy.select(stmt).compile()
                    except Exception:
                        pass
                    return self

                def all(self):
                    return []

            return await search_authoritative("100% 岗_位", _FakeDb())

        asyncio.run(_run())


class TestGenerateDefinition:
    """LLM 中文定义草案生成（修复：LLM 真正参与生成，失败回退原文）。"""

    class _FakeLLM:
        """返回固定定义草案文本的 LLM 桩。"""

        def __init__(self, text: str = "负责大语言模型相关系统的设计、开发与落地部署。"):
            self._text = text
            self.calls = 0

        def extract_structured(self, prompt, response_model, system_prompt=None, **kwargs):
            self.calls += 1
            return response_model(text=self._text)

    class _FailingLLM:
        def extract_structured(self, *args, **kwargs):
            raise RuntimeError("provider 全挂")

    @staticmethod
    def _run(coro):
        return asyncio.run(coro)

    def test_llm_generates_chinese_definition(self):
        """权威库命中时 LLM 把英文定义翻译为中文草案。"""
        llm = self._FakeLLM(text="负责开发与维护推荐系统算法。")
        occupation = {
            "code": "15-1252.00",
            "name": "Software Developers",
            "definition": "Design and develop software systems.",
        }
        draft = self._run(_generate_definition("推荐算法工程师", None, occupation, llm))
        assert draft.text == "负责开发与维护推荐系统算法。"
        assert draft.source == "llm"
        assert draft.nli_contradicted is False
        assert llm.calls == 1  # LLM 真实参与

    def test_llm_failure_falls_back_to_original(self):
        """LLM 失败静默回退权威库原文，不阻塞接地判定。"""
        occupation = {
            "code": "15-1252.00",
            "name": "Software Developers",
            "definition": "Design and develop software systems.",
        }
        draft = self._run(_generate_definition("软件开发工程师", None, occupation, self._FailingLLM()))
        assert draft.text == "Design and develop software systems."
        assert draft.source == "occupation"
        assert draft.nli_contradicted is False

    def test_seed_description_used_without_occupation(self):
        """仅种子命中时用种子描述作基座（LLM 可用则生成）。"""
        llm = self._FakeLLM(text="负责检索增强生成系统构建。")
        seed = {"name": "RAG 工程师", "description": "专注 RAG 系统构建"}
        draft = self._run(_generate_definition("RAG 工程师", seed, None, llm))
        assert draft.text == "负责检索增强生成系统构建。"
        assert draft.source == "llm"

    def test_no_reference_returns_empty(self):
        """无权威库/种子参考时返回空串（不触发 LLM）。"""
        llm = self._FakeLLM()
        draft = self._run(_generate_definition("未知岗位", None, None, llm))
        assert draft.text == ""
        assert llm.calls == 0

    def test_no_llm_falls_back_to_reference(self):
        """llm 为 None 时直接返回权威库原文（降级路径）。"""
        occupation = {
            "code": "15-1252.00",
            "name": "Software Developers",
            "definition": "Design and develop software systems.",
        }
        draft = self._run(_generate_definition("软件开发工程师", None, occupation, None))
        assert draft.text == "Design and develop software systems."
        assert draft.source == "occupation"

    def test_nli_contradiction_triggers_resample_then_fallback(self):
        """NLI 矛盾软门控：首稿含否定断言（可疑级）→ 触发重采样；重采样后
        仍可疑 → 截断回退参考原文 + 打标（幻觉防控核心闭环）。"""
        reference = "负责大数据平台架构设计与集群性能优化，要求熟悉分布式系统。"
        occupation = {"code": "15-1252.00", "name": "ML", "definition": reference}

        # 首稿含否定断言且与基座低重合（可疑级），重采样稿仍否定 → 回退原文 + 打标
        class _ContradictoryLLM:
            def __init__(self):
                self.calls = 0

            def extract_structured(self, prompt, response_model, system_prompt=None, **kwargs):
                self.calls += 1
                return response_model(text="该岗位无需任何编程能力。")

        llm = _ContradictoryLLM()
        draft = self._run(_generate_definition("ML", None, occupation, llm))
        assert draft.text == reference          # 截断回退参考原文
        assert draft.source == "occupation"
        assert draft.nli_contradicted is True   # 打标
        assert llm.calls == 2                   # 首稿 + 重采样各一次

    def test_nli_confirmed_contradiction_truncates_without_resample(self):
        """确认级矛盾（同主题否定极性翻转）→ 直接截断回退，不重采样。"""
        reference = "岗位要求具备 Python 编程能力，负责推荐系统开发。"
        occupation = {"code": "15-1252.00", "name": "ML", "definition": reference}

        class _ConfirmedContradictionLLM:
            def __init__(self):
                self.calls = 0

            def extract_structured(self, prompt, response_model, system_prompt=None, **kwargs):
                self.calls += 1
                return response_model(text="该岗位不需要 Python 编程能力。")

        llm = _ConfirmedContradictionLLM()
        draft = self._run(_generate_definition("ML", None, occupation, llm))
        assert draft.text == reference
        assert draft.nli_contradicted is True
        assert llm.calls == 1  # 确认矛盾直接截断，无重采样

    def test_nli_resample_recovers_clean_draft(self):
        """重采样后恢复为无矛盾草案 → 放行重采样文本（不截断）。"""
        reference = "岗位要求具备 Python 编程能力。"
        occupation = {"code": "15-1252.00", "name": "ML", "definition": reference}

        class _ResampleRecoveringLLM:
            def __init__(self):
                self.calls = 0

            def extract_structured(self, prompt, response_model, system_prompt=None, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return response_model(text="岗位不需要编程技能。")
                return response_model(text="负责 Python 编程与系统开发。")

        llm = _ResampleRecoveringLLM()
        draft = self._run(_generate_definition("ML", None, occupation, llm))
        assert draft.text == "负责 Python 编程与系统开发。"  # 重采样稿放行
        assert draft.source == "llm"
        assert draft.nli_contradicted is False
        assert llm.calls == 2

    def test_nli_degree_conflict_triggers_fallback(self):
        """学历量级冲突（参考本科，草案称高中）判为矛盾 → 回退参考原文。"""
        reference = "要求本科及以上学历，精通推荐算法。"
        occupation = {"code": "15-1252.00", "name": "ML", "definition": reference}

        class _DegreeConflictLLM:
            def __init__(self):
                self.calls = 0

            def extract_structured(self, prompt, response_model, system_prompt=None, **kwargs):
                self.calls += 1
                return response_model(text="高中毕业即可，无需任何算法经验。")

        llm = _DegreeConflictLLM()
        draft = self._run(_generate_definition("ML", None, occupation, llm))
        assert draft.text == reference
        assert draft.nli_contradicted is True

    def test_nli_clean_draft_passes_no_resample(self):
        """与参考一致的中文凝练稿（含"系统"等重叠词）→ 无矛盾信号直接放行。"""
        reference = "负责推荐系统的设计与开发。"
        occupation = {"code": "15-1252.00", "name": "ML", "definition": reference}
        llm = self._FakeLLM(text="负责推荐系统的开发与落地。")
        draft = self._run(_generate_definition("ML", None, occupation, llm))
        assert draft.text == "负责推荐系统的开发与落地。"
        assert draft.nli_contradicted is False
        assert llm.calls == 1  # 未触发重采样


class _FakeEmbedder:
    """固定相似度的假 embedder（embed 返回 384 维向量）。"""

    def __init__(self, similarity: float = 0.9):
        self._similarity = similarity

    def embed(self, text):
        return [0.1] * 384

    def similarity(self, a, b):
        return self._similarity


class _FakeDb:
    """假 AsyncSession：记录 stmt，返回预设行。fail=True 时恒抛。

    execute（语义路）返回 (occ, sim) 元组行，scalars（关键词路）返回
    Occupation 行——与真实 pgvector 查询返回形态对齐。
    """

    def __init__(self, rows=None, sem_rows=None, fail: bool = False):
        self._rows = rows or []
        self._sem_rows = sem_rows or []
        self._fail = fail
        self.stmts = []
        self._last = "scalars"

    async def scalars(self, stmt):
        self.stmts.append(stmt)
        if self._fail:
            raise RuntimeError("db down")
        self._last = "scalars"
        return self

    async def execute(self, stmt):
        self.stmts.append(stmt)
        if self._fail:
            raise RuntimeError("db down")
        self._last = "execute"
        return self

    def all(self):
        return self._sem_rows if self._last == "execute" else self._rows


class _FailingOnceDb:
    """execute（语义路）抛错、scalars（关键词路）正常的假 db。"""

    def __init__(self, rows):
        self._rows = rows
        self._calls = 0

    async def execute(self, stmt):
        self._calls += 1
        raise RuntimeError("vector 列缺失")

    async def scalars(self, stmt):
        return self

    def all(self):
        return self._rows


class _FakeNeo4jSession:
    def __init__(self, rows=None, fail: bool = False):
        self._rows = rows or []
        self._fail = fail
        self.query = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, query, **params):
        self.query = query
        if self._fail:
            raise RuntimeError("neo4j down")
        return self

    def data(self):
        return self._rows


class _FakeNeo4j:
    def __init__(self, session: _FakeNeo4jSession):
        self._session = session

    def session(self):
        return self._session


def _occ(code="15-1252.00", name="Software Developers", aliases=None):
    from app.models.business import Occupation

    return Occupation(
        code=code,
        name=name,
        category="Computer and Mathematical",
        definition="Design and develop software systems.",
        aliases=aliases or [],
    )


class TestThreadingWrappers:
    """ARQ 阻塞防护：同步阻塞调用（SBERT/Neo4j）必须经 asyncio.to_thread 执行。

    若改回直接同步调用，事件循环会被阻塞导致 ARQ 心跳超时；
    本测试通过记录 to_thread 被包进的函数守住该约束。
    """

    @staticmethod
    def _spy_to_thread(monkeypatch):
        from app.services.discovery import grounding

        called: list = []

        async def _wrapped(fn, *args, **kwargs):
            called.append(fn)
            return fn(*args, **kwargs)

        monkeypatch.setattr(grounding.asyncio, "to_thread", _wrapped)
        return called

    def test_fulltext_query_runs_in_thread(self, monkeypatch):
        """Neo4j 全文查询经 to_thread（_query_fulltext 被包入）。"""
        from app.services.discovery import grounding

        called = self._spy_to_thread(monkeypatch)
        neo4j = _FakeNeo4j(_FakeNeo4jSession(rows=[{
            "code": "15-1252.00", "name": "Software Developers",
            "category": "x", "definition": "d", "aliases": [], "score": 1.0,
        }]))
        hits = asyncio.run(grounding._neo4j_fulltext(neo4j, "软件开发", 10))
        assert hits
        assert grounding._query_fulltext in called

    def test_semantic_embedding_runs_in_thread(self, monkeypatch):
        """SBERT embed 经 to_thread（防阻塞事件循环）。"""
        from app.services.discovery import grounding

        embedder = _FakeEmbedder()
        called = self._spy_to_thread(monkeypatch)
        db = _FakeDb(sem_rows=[(_occ(), 0.9)])
        hits = asyncio.run(grounding._semantic_search(db, "软件开发", embedder, 10))
        assert hits
        assert embedder.embed in called  # qvec 计算经 to_thread


class TestFulltextSanitize:
    def test_strips_lucene_specials(self):
        assert sanitize_fulltext("C++ 工程师") == "C 工程师"
        assert sanitize_fulltext("岗位:(高级)") == "岗位高级"

    def test_empty_after_sanitize(self):
        assert sanitize_fulltext(":::") == ""


class TestMergeHits:
    def test_dedup_by_code_keeps_highest_score(self):
        hits = [
            {"code": "a", "score": 0.3},
            {"code": "b", "score": 0.9},
            {"code": "a", "score": 0.8},
        ]
        out = _merge_hits(hits, 5)
        assert [h["code"] for h in out] == ["b", "a"]
        assert out[1]["score"] == 0.8

    def test_truncates_to_limit(self):
        hits = [{"code": f"c{i}", "score": float(i)} for i in range(5)]
        assert len(_merge_hits(hits, 2)) == 2


class TestDualPathRetrieval:
    """双路检索（设计 7.2.3）：pgvector 语义 + Neo4j 全文，降级 ILIKE。"""

    def test_semantic_path_scores_and_normalizes(self):
        """语义路 score 与 pgvector 排序同源（1 - cosine_distance）。"""
        async def _run():
            db = _FakeDb(sem_rows=[(_occ(), 0.8)])
            hits = await search_authoritative("大模型应用工程师", db, embedder=_FakeEmbedder(0.8))
            assert len(hits) == 1
            assert hits[0]["code"] == "15-1252.00"
            assert hits[0]["score"] == pytest.approx(0.8)
            assert hits[0]["source"] == "semantic"
            assert hits[0]["name_hit"] is False  # 中文岗位名不子串命中英文名

        asyncio.run(_run())

    def test_semantic_error_degrades_to_keyword(self):
        """向量列缺失等异常 → 语义路降级，关键词路（ILIKE）仍返回。"""
        async def _run():
            db = _FailingOnceDb(rows=[_occ(name="软件开发")])
            hits = await search_authoritative("软件开发", db, embedder=_FakeEmbedder())
            assert len(hits) == 1
            assert hits[0]["source"] == "keyword"
            assert hits[0]["score"] == 1.0  # name 命中

        asyncio.run(_run())

    def test_neo4j_keyword_path_used_and_normalized(self):
        async def _run():
            rows = [
                {
                    "code": "15-1252.00",
                    "name": "Software Developers",
                    "category": "Computer and Mathematical",
                    "definition": "Design software.",
                    "aliases": ["Software Developers II"],
                    "score": 0.9,
                }
            ]
            db = _FakeDb([])
            hits = await search_authoritative(
                "Software Developers", db, neo4j=_FakeNeo4j(_FakeNeo4jSession(rows=rows))
            )
            assert len(hits) == 1
            assert hits[0]["source"] == "keyword"
            assert hits[0]["score"] == pytest.approx(0.9)
            assert hits[0]["name_hit"] is True
            assert hits[0]["alias_hits"] == ["Software Developers II"]

        asyncio.run(_run())

    def test_neo4j_unavailable_falls_back_to_ilike(self):
        async def _run():
            db = _FakeDb(rows=[_occ()])
            hits = await search_authoritative(
                "Software Developers", db, neo4j=_FakeNeo4j(_FakeNeo4jSession(fail=True))
            )
            assert len(hits) == 1
            assert hits[0]["source"] == "keyword"
            assert hits[0]["score"] == 1.0

        asyncio.run(_run())

    def test_merge_dedup_semantic_and_keyword(self):
        """同一 code 两路命中 → 合并去重保留高分（融合分并列时按原始分决胜）。"""
        async def _run():
            db = _FakeDb(sem_rows=[(_occ(), 0.8)])
            key_rows = [
                {
                    "code": "15-1252.00",
                    "name": "Software Developers",
                    "category": "",
                    "definition": "",
                    "aliases": [],
                    "score": 0.95,
                }
            ]
            hits = await search_authoritative(
                "大模型应用工程师",
                db,
                neo4j=_FakeNeo4j(_FakeNeo4jSession(rows=key_rows)),
                embedder=_FakeEmbedder(0.8),
            )
            assert len(hits) == 1
            assert hits[0]["source"] == "keyword"
            assert hits[0]["score"] == pytest.approx(0.95)

        asyncio.run(_run())

    def test_default_limit_is_10(self):
        """设计 7.2.3 top-10 口径：缺省 limit=10，12 条命中截断到 10。"""
        async def _run():
            db = _FakeDb(rows=[_occ(code=f"c{i}") for i in range(12)])
            hits = await search_authoritative("大模型应用工程师", db, embedder=_FakeEmbedder())
            assert len(hits) == 10

        asyncio.run(_run())

    def test_explicit_limit_override(self):
        """调用方显式 limit 覆盖默认值。"""
        async def _run():
            db = _FakeDb(rows=[_occ(code=f"c{i}") for i in range(12)])
            hits = await search_authoritative(
                "大模型应用工程师", db, embedder=_FakeEmbedder(), limit=3
            )
            assert len(hits) == 3

        asyncio.run(_run())



class TestDegradationObservability:
    """降级路径必须留下计数与 warning（第五轮审查 P1-5：静默降级可在生产潜伏数周）。"""

    def test_neo4j_fulltext_failure_counts_and_warns(self, monkeypatch, caplog):
        from app.services.discovery import grounding

        def _boom(*args, **kwargs):
            raise RuntimeError("neo4j down")

        monkeypatch.setattr(grounding, "_query_fulltext", _boom)

        async def _run():
            with caplog.at_level("WARNING", logger="app.services.discovery.grounding"):
                return await grounding._neo4j_fulltext(object(), "算法工程师", 10)

        before = grounding.degradation_counts["neo4j_fulltext"]
        assert asyncio.run(_run()) == []
        assert grounding.degradation_counts["neo4j_fulltext"] - before == 1
        assert any("neo4j_fulltext" in r.getMessage() for r in caplog.records)

    def test_semantic_failure_counts_and_still_returns_keyword(self, monkeypatch):
        from app.services.discovery import grounding

        async def _boom(*args, **kwargs):
            raise RuntimeError("embedder down")

        monkeypatch.setattr(grounding, "_semantic_search", _boom)

        async def _run():
            db = _FakeDb(rows=[_occ(name="软件开发")])
            return await search_authoritative("软件开发", db, embedder=_FakeEmbedder())

        before = grounding.degradation_counts["semantic_search"]
        hits = asyncio.run(_run())
        assert len(hits) == 1
        assert hits[0]["source"] == "keyword"
        assert grounding.degradation_counts["semantic_search"] - before == 1

    def test_redis_cache_failure_counts(self, monkeypatch):
        import app.core.database as database
        from app.services.discovery import grounding

        class _BrokenRedis:
            async def get(self, *args, **kwargs):
                raise RuntimeError("redis down")

            async def set(self, *args, **kwargs):
                raise RuntimeError("redis down")

        monkeypatch.setattr(database, "redis_client", _BrokenRedis())
        monkeypatch.setattr(grounding, "_CACHE_ENABLED", True)

        async def _run():
            got = await grounding._cache_get("软件开发", 10)
            await grounding._cache_set("软件开发", 10, [{"code": "c1"}])
            return got

        before_get = grounding.degradation_counts["redis_cache_get"]
        before_set = grounding.degradation_counts["redis_cache_set"]
        assert asyncio.run(_run()) is None
        assert grounding.degradation_counts["redis_cache_get"] - before_get == 1
        assert grounding.degradation_counts["redis_cache_set"] - before_set == 1
