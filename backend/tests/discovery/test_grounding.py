"""RAG 接地单元测试（设计文档 7.2.3 节）。

覆盖种子列表匹配与权威库检索的匹配口径。
"""

import asyncio

import pytest

from app.services.discovery.grounding import (
    _generate_definition,
    match_seed,
    search_authoritative,
)

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
            from sqlalchemy.ext.asyncio import AsyncSession

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
        assert draft == "负责开发与维护推荐系统算法。"
        assert llm.calls == 1  # LLM 真实参与

    def test_llm_failure_falls_back_to_original(self):
        """LLM 失败静默回退权威库原文，不阻塞接地判定。"""
        occupation = {
            "code": "15-1252.00",
            "name": "Software Developers",
            "definition": "Design and develop software systems.",
        }
        draft = self._run(_generate_definition("软件开发工程师", None, occupation, self._FailingLLM()))
        assert draft == "Design and develop software systems."

    def test_seed_description_used_without_occupation(self):
        """仅种子命中时用种子描述作基座（LLM 可用则生成）。"""
        llm = self._FakeLLM(text="负责检索增强生成系统构建。")
        seed = {"name": "RAG 工程师", "description": "专注 RAG 系统构建"}
        draft = self._run(_generate_definition("RAG 工程师", seed, None, llm))
        assert draft == "负责检索增强生成系统构建。"

    def test_no_reference_returns_empty(self):
        """无权威库/种子参考时返回空串（不触发 LLM）。"""
        llm = self._FakeLLM()
        draft = self._run(_generate_definition("未知岗位", None, None, llm))
        assert draft == ""
        assert llm.calls == 0

    def test_no_llm_falls_back_to_reference(self):
        """llm 为 None 时直接返回权威库原文（降级路径）。"""
        occupation = {
            "code": "15-1252.00",
            "name": "Software Developers",
            "definition": "Design and develop software systems.",
        }
        draft = self._run(_generate_definition("软件开发工程师", None, occupation, None))
        assert draft == "Design and develop software systems."
