"""向量预筛召回（阶段 C 性能修复）单元测试——mock embedder/redis，无真 SBERT。"""

import numpy as np

from app.services.matching.jd_vector_recall import (
    build_pool_vectors,
    candidate_vector,
    pool_profiles_fingerprint,
    vector_recall,
)
from app.services.matching.schemas import Necessity, PositionProfile, SkillRequirement


class _FakeEmbedder:
    """假 embedder：_cache 预置技能名 → 向量（绕过 SBERT）。"""

    def __init__(self, mapping: dict):
        self._cache = dict(mapping)

    def warm(self, names):
        pass  # 假缓存已全量命中


def _skill(name, necessity=Necessity.MUST):
    return SkillRequirement(skill_id=name, skill_name=name, necessity=necessity)


def _profile(jd_id, musts, nices=()):
    return PositionProfile(
        position_id=jd_id, name=f"JD-{jd_id}",
        must_skills=[_skill(s) for s in musts],
        nice_skills=[_skill(s, Necessity.NICE) for s in nices],
    )


def _vec(*components):
    """构造 384 维向量：前几维给定，余 0。"""
    v = np.zeros(384, dtype=np.float32)
    for i, c in enumerate(components):
        v[i] = c
    return v


class TestBuildPoolVectors:
    def test_pool_is_mean_of_skill_vectors(self):
        emb = _FakeEmbedder({"Java": _vec(1, 0), "Spring": _vec(0, 1)})
        profs = [_profile("1", ["Java", "Spring"])]
        out = build_pool_vectors(profs, emb)
        assert "1" in out
        pool = np.asarray(out["1"])
        # 平均：(1,0)与(0,1) → (0.5,0.5)
        assert abs(pool[0] - 0.5) < 1e-6 and abs(pool[1] - 0.5) < 1e-6

    def test_no_skills_jd_skipped(self):
        emb = _FakeEmbedder({"Java": _vec(1)})
        profs = [_profile("1", []), _profile("2", ["Java"])]
        out = build_pool_vectors(profs, emb)
        assert set(out) == {"2"}

    def test_sbert_unavailable_returns_none(self):
        # 缓存空 = warm 后仍无向量 → None（调用方降级）
        out = build_pool_vectors([_profile("1", ["Java"])], _FakeEmbedder({}))
        assert out is None


class TestCandidateVector:
    def test_mean_of_names(self):
        emb = _FakeEmbedder({"Python": _vec(1, 0), "Go": _vec(0, 1)})
        v = candidate_vector(["Python", "Go"], emb)
        assert abs(v[0] - 0.5) < 1e-6 and abs(v[1] - 0.5) < 1e-6

    def test_empty_names_none(self):
        assert candidate_vector([], _FakeEmbedder({})) is None

    def test_unknown_names_none(self):
        assert candidate_vector(["Rust"], _FakeEmbedder({"Java": _vec(1)})) is None


class TestVectorRecall:
    def test_orders_by_cosine(self):
        emb = _FakeEmbedder({
            "Java": _vec(1, 0), "Spring": _vec(0.9, 0.1),
            "Python": _vec(0, 1), "ML": _vec(0.1, 0.9),
        })
        profs = [
            _profile("1", ["Java", "Spring"]),   # 偏 x 轴
            _profile("2", ["Python", "ML"]),     # 偏 y 轴
        ]
        pool_vecs = build_pool_vectors(profs, emb)
        cand = candidate_vector(["Java"], emb)   # 候选偏 x 轴
        out = vector_recall(profs, pool_vecs, cand, k=2)
        assert [p.position_id for p in out] == ["1", "2"]

    def test_k_caps(self):
        emb = _FakeEmbedder({f"s{i}": _vec(1, i * 0.01) for i in range(10)})
        profs = [_profile(str(i), [f"s{i}"]) for i in range(10)]
        pool_vecs = build_pool_vectors(profs, emb)
        cand = candidate_vector(["s0"], emb)
        out = vector_recall(profs, pool_vecs, cand, k=3)
        assert len(out) == 3

    def test_jd_without_pool_vector_excluded(self):
        profs = [_profile("1", ["Java"]), _profile("2", ["Python"])]
        pool_vecs = {"1": list(_vec(1, 0))}  # 2 无池化向量
        out = vector_recall(profs, pool_vecs, list(_vec(1, 0)), k=5)
        assert [p.position_id for p in out] == ["1"]


class TestFingerprint:
    def test_changes_with_skill_set(self):
        a = pool_profiles_fingerprint([_profile("1", ["Java"])])
        b = pool_profiles_fingerprint([_profile("1", ["Java", "Spring"])])
        c = pool_profiles_fingerprint([_profile("1", ["Java"])])
        assert a != b and a == c
