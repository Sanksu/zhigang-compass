"""JD 级岗位匹配服务单元测试（jd_match.py，方案 A）。

覆盖：
- score_jd_compare：某岗位名下多 JD → 取真正最高分一条（best JD 口径）；
  无该岗位 JD → None；无 extraction 行跳过。
- load_jd_evidence_refs：技能 → JD 采集源证据引用（同源去重）。
- score_jd_auto 的 SBERT 降级路径（pool_vecs=None → rough_select 命中粗选）。
不触真实 DB / SBERT：JD 行用 fake async session 注入，评分用打桩 score_position。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.matching import jd_match
from app.services.matching.schemas import (
    CandidateProfile,
    CandidateSkill,
    MatchResult,
    PositionProfile,
)


@pytest.fixture(autouse=True)
def _no_embedding(monkeypatch):
    """全程禁用真实 SBERT：score_jd_compare/score_jd_auto 顶部会调 SkillEmbedder.get()，
    若打分/召回路径使用了它会触发真实模型下载（慢 / 网络）。这里让 get 返回惰性桩，
    被 stub 的消费点不再触真实模型。"""
    monkeypatch.setattr(
        "app.services.matching.semantic.SkillEmbedder.get",
        classmethod(lambda cls: _DummyEmbedder()),
    )


def _snap_with_extraction(position_name: str, skills: list[str], title: str = "") -> dict:
    return {
        "title": title or f"{position_name} JD",
        "normalized_position": position_name,
        "extraction": {
            "position_name": position_name,
            "skills": [{"name": s} for s in skills],
        },
    }


class _FakeSession:
    """按查询返回 JD 行。scalars(...).all() 返回注入 rows。

    filter_pos：模拟 SQL `normalized_position == x` 的子集过滤（真实 JD match 的
    compare 路径依赖该 where 条件；fake 无法可靠解析渲染后的 SQL，故显式注入）。
    filter_pos 为 None 时返回全部注入行（load_all_jd_profiles 全量路径）。
    """

    def __init__(self, rows: list[SimpleNamespace], filter_pos: str | None = None):
        self._rows = rows
        self._filter_pos = filter_pos

    async def scalars(self, stmt):
        if self._filter_pos is None:
            return SimpleNamespace(all=lambda: self._rows)
        filtered = [
            r for r in self._rows
            if (r.snapshot or {}).get("normalized_position") == self._filter_pos
        ]
        return SimpleNamespace(all=lambda: filtered)


def _row(position_name: str, skills: list[str], title: str = "", jd_id: int = 1) -> SimpleNamespace:
    snap = _snap_with_extraction(position_name, skills, title)
    return SimpleNamespace(
        id=jd_id, source="zhilian", source_url=f"https://x/{jd_id}",
        snapshot=snap,
    )

def _candidate(skills: list[str]) -> CandidateProfile:
    return CandidateProfile(
        user_id="u1",
        skills=[CandidateSkill(skill_id=s, skill_name=s, proficiency=2) for s in skills],
        total_years=5,
    )


class _DummyEmbedder:
    """占位 embedder：命中粗选路径下 candidate_vector/vector_recall 已被打桩跳过，
    评分消费点也被 stub（score_position / pool），此处不触真实 SBERT。"""

    def warm(self, names):
        pass

    @property
    def _cache(self):
        return {}


class TestScoreJdCompare:
    def test_picks_highest_scoring_jd(self, monkeypatch):
        """多 JD 评分后取最高分一条（best JD 口径）。"""
        rows = [
            _row("后端工程师", ["Java", "Spring"], title="后端 JD-基础", jd_id=1),
            _row("后端工程师", ["Python", "Docker", "K8s"], title="后端 JD-全栈", jd_id=2),
        ]
        probe: list[float] = []

        def _fake_score(candidate, profile: PositionProfile, *a, **kw):
            # 按 JD 标题映射分数：基础 JD→0.7，全栈 JD→0.92
            score = 0.7 if profile.name == "后端 JD-基础" else 0.92
            probe.append(score)
            return MatchResult(
                position_id=profile.position_id,
                position_name=profile.name,
                total_score=score,
                must_score=score, nice_score=1.0, exp_score=1.0,
                missing_must=[], matched_must=[],
            )

        monkeypatch.setattr(jd_match, "score_position", _fake_score)
        best, result = asyncio.run(jd_match.score_jd_compare(
            _FakeSession(rows), _candidate(["Python", "Docker"]), "后端工程师", {},
        ))
        # 高分 JD（全栈，技能更多）胜出：result=0.92，best 为全栈 JD
        assert result.total_score == 0.92
        assert best.name == "后端 JD-全栈"
        assert "Docker" in [s.skill_name for s in best.must_skills]
        assert 0.7 in probe and 0.92 in probe

    def test_no_position_jd_returns_none(self, monkeypatch):
        rows = [_row("算法工程师", ["PyTorch"], jd_id=1)]
        # filter_pos 过滤后无匹配行 → score_jd_compare 返回 None
        out = asyncio.run(jd_match.score_jd_compare(
            _FakeSession(rows, filter_pos="不存在的岗位"),
            _candidate(["Python"]), "不存在的岗位", {},
        ))
        assert out is None

    def test_skips_rows_without_extraction(self, monkeypatch):
        """无 extraction 的 JD 行跳过（不参与评分）。"""
        ok_row = _row("后端工程师", ["Java", "Spring"], jd_id=1)
        no_ext_row = SimpleNamespace(
            id=2, source="x", source_url="", snapshot={"normalized_position": "后端工程师"},
        )
        seen: list[str] = []

        def _fake_score(candidate, profile: PositionProfile, *a, **kw):
            seen.append(profile.name)
            return MatchResult(
                position_id=profile.position_id, position_name=profile.name,
                total_score=0.5, must_score=0.5, nice_score=1.0, exp_score=1.0,
            )

        monkeypatch.setattr(jd_match, "score_position", _fake_score)
        best, _ = asyncio.run(jd_match.score_jd_compare(
            _FakeSession([ok_row, no_ext_row]), _candidate(["Java"]), "后端工程师", {},
        ))
        # 只有 1 条带 extraction 的参与评分（无 extraction 行跳过）
        assert seen == ["后端工程师 JD"]


class TestLoadJdEvidenceRefs:
    def test_skill_to_source_mapping_dedup(self, monkeypatch):
        """技能 → 采集源证据（同技能每源至多 1 条）。"""
        rows = [
            _row("后端工程师", ["Java", "MySQL"], title="t1", jd_id=1),
            _row("后端工程师", ["Java", "Redis"], title="t2", jd_id=2),
        ]
        refs = asyncio.run(jd_match.load_jd_evidence_refs(
            _FakeSession(rows), "后端工程师",
        ))
        skills = {r["skill"] for r in refs}
        assert "Java" in skills and "MySQL" in skills and "Redis" in skills
        java = next(r for r in refs if r["skill"] == "Java")
        assert java["source"] == "zhilian" and java["url"].endswith("/1")


class TestScoreJdAutoRoughFallback:
    def test_pool_none_falls_back_to_rough_select(self, monkeypatch):
        """SBERT 池化不可用（pool_vecs=None）→ 命中粗选，不崩溃。"""
        rows = [
            _row("后端工程师", ["Python", "Docker"], jd_id=1),
            _row("算法工程师", ["PyTorch", "Transformers"], jd_id=2),
        ]
        # 池化不可用：降级 rough_select 技能命中粗选
        monkeypatch.setattr(jd_match, "load_pool_vectors_cached", _fake_pool_none)
        monkeypatch.setattr(jd_match, "candidate_vector", lambda *a, **k: None)
        monkeypatch.setattr(jd_match, "vector_recall", lambda *a, **k: None)

        def _fake_score(candidate, profile: PositionProfile, *a, **kw):
            return MatchResult(
                position_id=profile.position_id, position_name=profile.name,
                total_score=0.6, must_score=0.6, nice_score=1.0, exp_score=1.0,
                matched_must=[], missing_must=[],
            )

        monkeypatch.setattr(jd_match, "score_position", _fake_score)
        scored = asyncio.run(jd_match.score_jd_auto(
            _FakeSession(rows), _candidate(["Python"]), {},
            top_n=10, rough_k=50,
            semantic=_DummyEmbedder(),
        ))
        # 候选 Python 命中「后端工程师」→ 聚合出岗位级结果（池化降级路径不崩）
        assert isinstance(scored, list)
        assert all("position_name" in s for s in scored)


class TestNarrowJdFilter:
    """08-27 窄 JD 防虚高：单技能窄 JD 命中即满分会抹平区分度，须过滤（默认阈值 2）。"""

    def test_compare_filters_narrow_single_skill_jd(self, monkeypatch):
        """compare 路径：单技能窄 JD 不参与评分，取宽 JD 真最高分。"""
        rows = [
            _row("后端工程师", ["Java", "Spring"], title="后端 JD-宽", jd_id=1),
            _row("后端工程师", ["Python"], title="后端 JD-窄", jd_id=2),
        ]
        scored_names: list[str] = []

        def _fake_score(candidate, profile: PositionProfile, *a, **kw):
            scored_names.append(profile.name)
            # 窄 JD 若被误评分会得高分 0.99，但不应出现在结果
            score = 0.99 if profile.name == "后端 JD-窄" else 0.7
            return MatchResult(
                position_id=profile.position_id, position_name=profile.name,
                total_score=score, must_score=score, nice_score=1.0, exp_score=1.0,
                matched_must=[], missing_must=[],
            )

        monkeypatch.setattr(jd_match, "score_position", _fake_score)
        best, result = asyncio.run(jd_match.score_jd_compare(
            _FakeSession(rows), _candidate(["Python"]), "后端工程师", {},
        ))
        assert "后端 JD-窄" not in scored_names  # 窄 JD 未参与评分
        assert result.total_score == 0.7
        assert best.name == "后端 JD-宽"

    def test_auto_filters_narrow_jd_from_pool(self, monkeypatch):
        """recommend 路径：单技能窄 JD 不进召回池（防列表虚高），宽 JD 正常。"""
        rows = [
            _row("后端工程师", ["Python", "Docker"], title="后端-宽", jd_id=1),
            _row("后端工程师", ["Python"], title="后端-窄", jd_id=2),
            _row("算法工程师", ["PyTorch", "NLP"], jd_id=3),
        ]
        seen: list[str] = []

        def _fake_score(candidate, profile: PositionProfile, *a, **kw):
            seen.append(profile.name)
            return MatchResult(
                position_id=profile.position_id, position_name=profile.name,
                total_score=0.6, must_score=0.6, nice_score=1.0, exp_score=1.0,
                matched_must=[], missing_must=[],
            )

        monkeypatch.setattr(jd_match, "load_pool_vectors_cached", _fake_pool_none)
        monkeypatch.setattr(jd_match, "candidate_vector", lambda *a, **k: None)
        monkeypatch.setattr(jd_match, "vector_recall", lambda *a, **k: None)
        monkeypatch.setattr(jd_match, "score_position", _fake_score)
        out = asyncio.run(jd_match.score_jd_auto(
            _FakeSession(rows), _candidate(["Python"]), {},
            top_n=10, rough_k=50, semantic=_DummyEmbedder(),
        ))
        assert "后端-窄" not in seen  # 单技能窄 JD 不进池
        assert isinstance(out, list)
        assert all("position_name" in s for s in out)


    def test_duplicate_skill_across_must_nice_counts_once(self, monkeypatch):
        """同名技能在 skills[] 与 requirements[] 双列出现只算 1 个宽度（防撑宽漏过滤）。

        08-27 E2E 实测：如 Kafka 同时进 skills 和 requirements，按条数会算成 2 而
        漏过窄 JD 过滤，导致单技能 JD 命中即满分虚高。
        """
        rows = [_row("后端工程师", ["Java", "Spring"], title="宽 JD", jd_id=1)]
        snap = _snap_with_extraction("后端工程师", ["Java"], title="窄 JD")
        snap["extraction"]["requirements"] = [{"skill_name": "Java"}]
        narrow = SimpleNamespace(id=2, source="x", source_url="", snapshot=snap)
        scored_names: list[str] = []

        def _fake_score(candidate, profile: PositionProfile, *a, **kw):
            scored_names.append(profile.name)
            return MatchResult(
                position_id=profile.position_id, position_name=profile.name,
                total_score=0.5, must_score=0.5, nice_score=1.0, exp_score=1.0,
            )

        monkeypatch.setattr(jd_match, "score_position", _fake_score)
        asyncio.run(jd_match.score_jd_compare(
            _FakeSession([rows[0], narrow]), _candidate(["Java"]), "后端工程师", {},
        ))
        assert "窄 JD" not in scored_names  # 单技能(双列重复)JD 被过滤


    def test_compare_caps_scored_jds_per_position(self, monkeypatch):
        """每岗位评分上限：宽 JD 超过上限时只评最近 N 条（防 recommend 对齐超时）。

        08-27 E2E：不设上限会对岗位全量 JD 评分（后端开发工程师 850 条 → recommend
        对齐 133s 超时），上限默认 50（runtime_config.match_jd_max_per_position 可调）。
        """
        rows = [
            _row("后端工程师", ["Java", "Spring"], title=f"JD-{i}", jd_id=i)
            for i in range(60)
        ]
        scored_count = {"n": 0}

        def _fake_score(candidate, profile: PositionProfile, *a, **kw):
            scored_count["n"] += 1
            return MatchResult(
                position_id=profile.position_id, position_name=profile.name,
                total_score=0.5, must_score=0.5, nice_score=1.0, exp_score=1.0,
            )

        monkeypatch.setattr(jd_match, "score_position", _fake_score)
        monkeypatch.setattr(jd_match, "_read_max_jds_per_position", lambda: 50)
        asyncio.run(jd_match.score_jd_compare(
            _FakeSession(rows), _candidate(["Java"]), "后端工程师", {},
        ))
        assert scored_count["n"] == 50  # 只评最近 50 条宽 JD，不是全量 60


class TestAlignScoresWithFullJd:
    """08-27 fix：Top-N 与 compare 对齐——召回池外最佳 JD 补入取真最高分。"""

    def test_full_jd_best_raises_score_and_evidence(self, monkeypatch):
        """池内最高 0.7 → 全量（含池外）最高 0.92：分数/证据被真最高分 JD 覆盖。"""
        results = [{
            "position_id": "后端工程师",
            "position_name": "后端工程师",
            "total_score": 0.7, "must_score": 0.7, "nice_score": 1.0, "exp_score": 1.0,
            "matched_must": ["Java"], "missing_must": ["Spring"],
            "summary": "池内最佳", "unqualified": False,
            "jd_evidence": [{"jd_id": "1", "jd_title": "JD-基础", "total_score": 0.7, "hit_count": 1}],
        }]
        best = MatchResult(
            position_id="2", position_name="JD-全栈", total_score=0.92,
            must_score=0.92, nice_score=1.0, exp_score=1.0,
            matched_must=["Java", "Spring"], missing_must=[], matched_nice=["Docker"],
            summary="全量最佳", unqualified=False,
        )

        async def _fake_compare(session, candidate, position_name, project_vectors, semantic=None, sim_threshold=None):
            return (None, best)

        monkeypatch.setattr(jd_match, "score_jd_compare", _fake_compare)
        out = asyncio.run(jd_match._align_scores_with_full_jd(
            None, _candidate(["Java", "Spring"]), results, {},
        ))
        assert out[0]["total_score"] == 0.92
        assert out[0]["must_score"] == 0.92
        assert out[0]["summary"] == "全量最佳"
        assert out[0]["position_id"] == "后端工程师"  # 对外岗位名不变
        assert out[0]["jd_evidence"][0]["jd_id"] == "2"  # 真最高分 JD 置顶
        assert out[0]["jd_evidence"][0]["hit_count"] == 3  # must(2)+nice(1)

    def test_no_position_jd_keeps_pool_result(self, monkeypatch):
        """某岗位无 JD（score_jd_compare 返回 None）→ 保留池内聚合结果。"""
        results = [{
            "position_id": "后端工程师", "position_name": "后端工程师",
            "total_score": 0.6, "must_score": 0.6, "nice_score": 1.0, "exp_score": 1.0,
            "matched_must": [], "missing_must": [], "summary": "s", "unqualified": False,
            "jd_evidence": [{"jd_id": "1", "jd_title": "JD-基础", "total_score": 0.6, "hit_count": 1}],
        }]

        async def _fake_compare(session, candidate, position_name, project_vectors, semantic=None, sim_threshold=None):
            return None

        monkeypatch.setattr(jd_match, "score_jd_compare", _fake_compare)
        out = asyncio.run(jd_match._align_scores_with_full_jd(
            None, _candidate(["Java"]), results, {},
        ))
        assert out[0]["total_score"] == 0.6
        assert out[0]["jd_evidence"][0]["jd_id"] == "1"


async def _fake_pool_none(profiles, embedder, redis_client=None):
    return None
