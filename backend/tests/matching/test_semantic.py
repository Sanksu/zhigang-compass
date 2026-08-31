"""余弦相似度与语义模块纯逻辑单元测试（M5 测试补充）。

覆盖 semantic.py 中与模型加载无关的纯函数与边界逻辑：
- cosine_similarity：纯 Python 余弦相似度计算（数值边界、退化情况、维度一致性）
- SemanticUnavailableError：异常类型语义
- SkillEmbedder 单例模式与缓存行为（mock 模型，不触发真实加载）

设计原则：
1. 数学正确性：相同向量=1、正交=0、反向=-1（SBERT 输出非负，实际在[0,1]）
2. 退化防护：零向量返回 0 而非 NaN
3. 维度不匹配：短向量截断匹配（zip 语义），不抛异常
4. 单例与缓存：get() 幂等、warm() 批量预热、空输入安全
"""

import pytest

from app.services.matching.semantic import (
    MODEL_NAME,
    SemanticUnavailableError,
    SkillEmbedder,
    cosine_similarity,
)


class TestCosineSimilarity:
    """cosine_similarity 纯函数：数学正确性 + 边界防护。"""

    def test_identical_vectors_return_one(self):
        """相同向量 → 相似度 = 1.0。"""
        vec = [1.0, 0.0, 0.0]
        assert cosine_similarity(vec, vec) == pytest.approx(1.0)

    def test_orthogonal_vectors_return_zero(self):
        """正交向量 → 相似度 = 0.0。"""
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors_return_negative_one(self):
        """完全相反向量 → 相似度 = -1.0。"""
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_result_in_minus_one_to_one_range(self):
        """余弦相似度恒在 [-1, 1] 区间内。"""
        import random
        rng = random.Random(42)
        for _ in range(20):
            a = [rng.uniform(-1, 1) for _ in range(10)]
            b = [rng.uniform(-1, 1) for _ in range(10)]
            sim = cosine_similarity(a, b)
            assert -1.0 <= sim <= 1.0, f"sim={sim} 超出 [-1,1] 范围"

    def test_zero_vector_a_returns_zero(self):
        """第一个向量为零向量 → 0.0（不抛异常，不报 NaN）。"""
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_zero_vector_b_returns_zero(self):
        """第二个向量为零向量 → 0.0。"""
        assert cosine_similarity([1.0, 2.0], [0.0, 0.0]) == 0.0

    def test_both_zero_vectors_return_zero(self):
        """两个都是零向量 → 0.0。"""
        assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0

    def test_single_dimension_vectors(self):
        """单维度向量也能正确计算。"""
        assert cosine_similarity([5.0], [3.0]) == pytest.approx(1.0)
        assert cosine_similarity([5.0], [-3.0]) == pytest.approx(-1.0)

    def test_high_dimensional_vectors(self):
        """高维向量（模拟 384 维 SBERT 输出）数值稳定。"""
        # 构造两个 384 维向量：完全相同 → 1.0
        vec = [0.123] * 384
        assert cosine_similarity(vec, vec) == pytest.approx(1.0)

    def test_scaling_does_not_change_similarity(self):
        """向量缩放不影响余弦相似度（方向不变）。"""
        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0, 6.0]
        sim_original = cosine_similarity(a, b)
        sim_scaled = cosine_similarity([x * 10 for x in a], [x * 0.5 for x in b])
        assert sim_original == pytest.approx(sim_scaled)

    def test_equal_dimension_384_sbert_like(self):
        """384 维向量（SBERT 典型输出维度）计算正确。"""
        # 构造两个已知向量：a = [1,0,0,...], b = [0,1,0,...] → 正交 = 0
        a = [0.0] * 384
        b = [0.0] * 384
        a[0] = 1.0
        b[1] = 1.0
        assert cosine_similarity(a, b) == pytest.approx(0.0)
        # a 与自身 = 1.0
        assert cosine_similarity(a, a) == pytest.approx(1.0)

    def test_empty_vectors_return_zero(self):
        """空向量列表 → 0.0（防御性）。"""
        assert cosine_similarity([], []) == 0.0
        assert cosine_similarity([], [1.0]) == 0.0
        assert cosine_similarity([1.0], []) == 0.0

    def test_integer_vectors_work(self):
        """整数向量也能正确计算（不要求 float）。"""
        assert cosine_similarity([3, 4], [3, 4]) == pytest.approx(1.0)

    def test_symmetry(self):
        """相似度对称：sim(a,b) == sim(b,a)。"""
        a = [0.5, -0.3, 0.8, 0.1]
        b = [-0.2, 0.7, 0.4, -0.6]
        assert cosine_similarity(a, b) == pytest.approx(cosine_similarity(b, a))

    def test_known_value_3d(self):
        """3 维已知值验证。"""
        # [1,1,0] 与 [1,0,1] 的夹角余弦 = 1/(√2 × √2) = 0.5
        assert cosine_similarity([1.0, 1.0, 0.0], [1.0, 0.0, 1.0]) == pytest.approx(0.5)


class TestSemanticUnavailableError:
    """异常类型语义验证。"""

    def test_is_exception(self):
        """SemanticUnavailableError 继承自 Exception。"""
        assert issubclass(SemanticUnavailableError, Exception)

    def test_can_be_raised_and_caught(self):
        """可正常抛出和捕获。"""
        with pytest.raises(SemanticUnavailableError) as exc_info:
            raise SemanticUnavailableError("模型加载失败")
        assert "模型加载失败" in str(exc_info.value)


class TestSkillEmbedderSingleton:
    """SkillEmbedder 单例模式（不加载真实模型）。"""

    def test_get_returns_same_instance(self):
        """get() 多次调用返回同一实例。"""
        a = SkillEmbedder.get()
        b = SkillEmbedder.get()
        assert a is b

    def test_new_instance_has_empty_cache(self):
        """新实例缓存为空。"""
        embedder = SkillEmbedder()
        assert embedder._cache == {}
        assert embedder._model is None

    def test_model_name_constant(self):
        """模型名称常量符合设计文档 9.3 指定。"""
        assert MODEL_NAME == "paraphrase-multilingual-MiniLM-L12-v2"


class TestSkillEmbedderWarm:
    """warm() 批量预热行为（mock 模型，避免真实加载）。"""

    def test_warm_empty_list_does_nothing(self):
        """空列表输入不触发模型加载，直接返回。"""
        embedder = SkillEmbedder()
        embedder.warm([])
        assert embedder._cache == {}

    def test_warm_all_cached_skips_model_load(self):
        """所有技能都已缓存时，不触发模型加载。"""
        embedder = SkillEmbedder()
        embedder._cache["Python"] = [0.1, 0.2, 0.3]
        embedder.warm(["Python"])
        # 缓存不变，且 model 仍为 None（没触发加载）
        assert embedder._model is None
        assert "Python" in embedder._cache

    def test_warm_strips_whitespace(self):
        """技能名前后空白被剥离后再查缓存。"""
        embedder = SkillEmbedder()
        embedder._cache["Python"] = [0.1, 0.2, 0.3]
        embedder.warm(["  Python  "])
        assert embedder._model is None  # 已缓存，不加载

    def test_warm_skips_empty_strings(self):
        """空字符串技能名被跳过。"""
        embedder = SkillEmbedder()
        embedder.warm(["", "   ", None])  # type: ignore[list-item]
        assert embedder._cache == {}
        assert embedder._model is None

    def test_warm_model_unavailable_silently_ignored(self):
        """模型不可用时 warm 静默忽略，不抛异常。"""
        embedder = SkillEmbedder()
        # _load 会尝试 import sentence_transformers，在测试环境可能没有
        # warm 应该捕获异常并静默返回
        try:
            embedder.warm(["Python", "Java"])
        except Exception as e:
            pytest.fail(f"warm() 不应抛出异常: {e}")
        # 无论模型是否可用，函数都应该正常返回


class TestSkillEmbedderPreload:
    """preload() 预热行为。"""

    def test_preload_never_raises(self):
        """preload 即使模型不可用也不抛异常（静默吞异常）。"""
        embedder = SkillEmbedder()
        try:
            embedder.preload()
        except Exception as e:
            pytest.fail(f"preload() 不应抛出异常: {e}")


class TestSkillEmbedderCacheEviction:
    """P2-10（第八轮）：名称向量缓存 LRU 上限（OrderedDict + 锁，8192 条封顶）。"""

    def test_cache_evicts_oldest_beyond_cap(self, monkeypatch):
        """超过上限淘汰最久未用项，缓存大小封顶。"""
        from app.services.matching import semantic

        monkeypatch.setattr(semantic, "_CACHE_MAX_ENTRIES", 2)
        embedder = SkillEmbedder()
        embedder._cache_put("a", [1.0])
        embedder._cache_put("b", [2.0])
        embedder._cache_put("c", [3.0])
        assert list(embedder._cache.keys()) == ["b", "c"]  # a（最旧）被淘汰

    def test_cache_hit_renews_recency(self, monkeypatch):
        """命中续期：再次访问的条目不被淘汰（LRU 而非 FIFO）。"""
        from app.services.matching import semantic

        monkeypatch.setattr(semantic, "_CACHE_MAX_ENTRIES", 2)
        embedder = SkillEmbedder()
        embedder._cache_put("a", [1.0])
        embedder._cache_put("b", [2.0])
        embedder._cache_get("a")  # a 续期 → b 成为最久未用
        embedder._cache_put("c", [3.0])
        assert list(embedder._cache.keys()) == ["a", "c"]  # b 被淘汰

    def test_cache_thread_safe_under_concurrent_puts(self, monkeypatch):
        """并发写不破坏结构（_vec/similarity 经 asyncio.to_thread 并发访问场景）。"""
        import threading

        from app.services.matching import semantic

        monkeypatch.setattr(semantic, "_CACHE_MAX_ENTRIES", 64)
        embedder = SkillEmbedder()

        def worker(i: int) -> None:
            for j in range(200):
                embedder._cache_put(f"k{i}-{j}", [float(j)])

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(embedder._cache) <= 64
