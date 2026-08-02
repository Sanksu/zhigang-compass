"""SBERT 语义模块单元测试（AL-M3-03）。

用假模型/失败路径验证，不依赖真实模型文件（CI 环境无模型缓存）：
- 模型加载失败 → SemanticUnavailableError（匹配引擎降级规则）
- 余弦相似度计算正确 + 向量缓存
"""

import pytest

from app.services.matching.semantic import (
    SemanticUnavailableError,
    SkillEmbedder,
)


class _FakeModel:
    """固定向量矩阵的假模型（encode 返回行向量）。"""

    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = vectors

    def encode(self, texts: list[str]):
        import numpy as np

        return np.array([self.vectors[t] for t in texts])


class TestSkillEmbedder:
    def test_load_failure_raises_unavailable(self, monkeypatch):
        emb = SkillEmbedder()

        def _fail(self):
            raise SemanticUnavailableError("SBERT 模型加载失败: 无网络")

        monkeypatch.setattr(SkillEmbedder, "_load", _fail)
        with pytest.raises(SemanticUnavailableError):
            emb.similarity("Python", "Go")

    def test_similarity_cosine(self, monkeypatch):
        """余弦相似度计算：同向 1.0，正交 0。"""
        emb = SkillEmbedder()
        model = _FakeModel({
            "A": [1.0, 0.0],
            "B": [0.0, 1.0],
            "C": [1.0, 0.0],
        })

        def _load(self):
            return model

        monkeypatch.setattr(SkillEmbedder, "_load", _load)
        assert emb.similarity("A", "C") == pytest.approx(1.0)
        assert emb.similarity("A", "B") == pytest.approx(0.0)

    def test_vectors_cached(self, monkeypatch):
        """同名向量只编码一次（缓存命中）。"""
        emb = SkillEmbedder()
        model = _FakeModel({"X": [1.0, 0.0]})
        load_count = {"n": 0}

        def _load(self):
            load_count["n"] += 1
            return model

        monkeypatch.setattr(SkillEmbedder, "_load", _load)
        assert emb.similarity("X", "X") == 1.0
        assert emb.similarity("X", "X") == 1.0
        # _load 仅触发一次（模型单次加载），向量编码走缓存
        assert load_count["n"] == 1

    def test_get_returns_singleton(self):
        assert SkillEmbedder.get() is SkillEmbedder.get()
