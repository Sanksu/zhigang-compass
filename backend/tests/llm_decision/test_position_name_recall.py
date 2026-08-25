"""岗位名候选语义召回测试（PositionCandidateRecaller，回流自实验场 e0d53ab）。

embedding 路径用注入桩验证排序与降级语义；不加载真模型（CI 无缓存）。
"""

from app.services.llm_decision.position_name import PositionCandidateRecaller
from app.services.matching.semantic import SemanticUnavailableError, SkillEmbedder


class _FakeEmbedder:
    """确定性桩：文本 → 预设向量（SkillEmbedder.get 鸭子类型）。"""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    def embed(self, text: str) -> list:
        return self._vectors[text.strip()]


_POOL = ["前端开发工程师", "算法工程师", "大数据开发工程师", "运维工程师"]


class TestRecallEmbedding:
    def test_topk_semantic_order(self, monkeypatch):
        fake = _FakeEmbedder({
            "前端开发工程师": [1.0, 0.0, 0.0],
            "算法工程师": [0.0, 1.0, 0.0],
            "大数据开发工程师": [0.0, 0.0, 1.0],
            "运维工程师": [0.6, 0.6, 0.0],
            "Web前端开发（React方向）": [0.95, 0.05, 0.0],
        })
        monkeypatch.setattr(
            SkillEmbedder, "get", classmethod(lambda cls: fake),
        )
        r = PositionCandidateRecaller(_POOL, k=2)
        assert r.mode == "embedding"
        assert r.recall("Web前端开发（React方向）")[:1] == ["前端开发工程师"]

    def test_k_truncates(self, monkeypatch):
        fake = _FakeEmbedder({
            "前端开发工程师": [1.0, 0.0],
            "算法工程师": [0.9, 0.1],
            "大数据开发工程师": [0.8, 0.2],
            "运维工程师": [0.7, 0.3],
            "q": [1.0, 0.0],
        })
        monkeypatch.setattr(SkillEmbedder, "get", classmethod(lambda cls: fake))
        r = PositionCandidateRecaller(_POOL, k=2)
        assert len(r.recall("q")) == 2


class TestRecallFallback:
    def test_model_unavailable_degrades_to_prefix(self, monkeypatch):
        class _Broken:
            def embed(self, text):
                raise SemanticUnavailableError("model not downloaded")

        monkeypatch.setattr(SkillEmbedder, "get", classmethod(lambda cls: _Broken()))
        r = PositionCandidateRecaller(_POOL, k=2)
        assert r.mode == "pool-prefix"
        assert r.recall("任意标题") == _POOL[:2]

    def test_small_pool_returns_all(self, monkeypatch):
        fake = _FakeEmbedder({"前端开发工程师": [1.0]})
        monkeypatch.setattr(SkillEmbedder, "get", classmethod(lambda cls: fake))
        r = PositionCandidateRecaller(["前端开发工程师"], k=20)
        assert r.mode == "pool-full"
        assert r.recall("x") == ["前端开发工程师"]

    def test_per_call_failure_returns_prefix(self, monkeypatch):
        """池编码成功后单条 embed 失败：回退池前缀而非抛错（shadow 不阻塞）。"""
        class _Flaky:
            def __init__(self):
                self.calls = 0

            def embed(self, text):
                self.calls += 1
                if self.calls <= 4:  # 池 4 名编码成功
                    return [1.0, 0.0]
                raise SemanticUnavailableError("flaky")

        monkeypatch.setattr(SkillEmbedder, "get", classmethod(lambda cls: _Flaky()))
        r = PositionCandidateRecaller(_POOL, k=2)
        assert r.mode == "embedding"
        assert r.recall("新标题") == _POOL[:2]
