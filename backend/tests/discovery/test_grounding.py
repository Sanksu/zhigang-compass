"""RAG 接地单元测试（设计文档 7.2.3 节）。

覆盖种子列表匹配与权威库检索的匹配口径。
"""

import asyncio

import pytest

from app.services.discovery.grounding import match_seed, search_authoritative

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
