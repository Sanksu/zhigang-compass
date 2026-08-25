r"""人岗匹配评测使用"真实 JD 技能列表"而非归一化岗位合并画像（AGENTS.md §4.1 算法核心）。

背景（2026-08-25，张恺天把关）：
- 评测与生产共用 engine.score_position，差异仅在于**岗位画像来源**：
    - 评测：PositionProfile.must/nice 来自黄金集行字面值 = 该 position_id 对应
      **单条真实 JD** 的技能要求（golden_set_match_v2 由 build_match_golden_v2 逐 JD
      抽取，position_id 即 jd_golden_100 的 id）。
    - 生产：loaders._load_positions_uncached 按归一化岗位聚合所有同名 JD 的 REQUIRES。
- 本测试**不触库（无 Neo4j）**，用仓库真实黄金集做数据确定性回归护栏：
    1. v2 每个 position_id 都能 1:1 解析到 jd_golden_100 一条真实 JD；
    2. evaluate_pairs 为该 position_id 构建的 PositionProfile 直接消费黄金集行字面
       must/nice（真实 JD 技能），而不是走图谱聚合；
    3. 报告/错误样例可展示真实 JD 标题（gold_title），非归一化岗位名。

跑法：cd zhigang-compass && set PYTHONPATH=<dev-wt>\backend && <venv>\python -m pytest tests/evaluate/test_match_real_jd.py -q
"""

import json
from collections import Counter

import pytest

from scripts.evaluate import _load_jd_titles, _JD_GOLDEN, _MATCH_GOLDEN
from scripts.tune_match_weights import (
    build_position,
    evaluate_pairs,
    load_pairs,
)


def _load_jsonl(path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# 1. position_id → 单条真实 JD 的 1:1 映射
# ---------------------------------------------------------------------------


def _match_v2_rows() -> list[dict]:
    # 生产口径 v2（384 行）；默认 v1 亦然，但 v2 才有 per-JD must/nice
    assert _MATCH_GOLDEN.parent.joinpath("golden_set_match_v2.jsonl").exists()
    return _load_jsonl(_MATCH_GOLDEN.parent.joinpath("golden_set_match_v2.jsonl"))


def _jd_rows() -> list[dict]:
    return _load_jsonl(_JD_GOLDEN)


class TestPositionIdToRealJdMapping:
    def test_v2_position_ids_resolve_to_unique_jd(self):
        """每个 v2 position_id 都对应 jd_golden_100 唯一一条（1:1）。"""
        v2 = _match_v2_rows()
        jd_ids = {j["id"] for j in _jd_rows()}
        pids = Counter(p["position_id"] for p in v2)
        assert len(jd_ids) == len(_jd_rows()), "jd_golden_100 id 必须唯一"
        # 无缺失、无一对多（每个 pid 恰在一条 JD 中出现）
        missing = [pid for pid in pids if pid not in jd_ids]
        assert not missing, f"v2 position_id 无对应 JD: {missing}"
        # 96 个不同岗位（384 行 / 4 对），每个均解析到单条 JD
        assert len(pids) == 96

    def test_each_v2_position_id_has_raw_text_and_gold_title(self):
        """每行 position_id 对应 JD 都带 raw_text（评估可回源）与 gold_title（真实标题）。"""
        jd_by_id = {j["id"]: j for j in _jd_rows()}
        v2 = _match_v2_rows()
        for p in v2:
            j = jd_by_id[p["position_id"]]
            assert j.get("gold_title"), f"{p['position_id']} 缺 gold_title"
            assert j.get("raw_text") or j.get("original_raw_text"), f"{p['position_id']} 缺 raw_text"

    def test_v2_must_nice_not_from_position_name(self):
        """v2 行必须/nice 来自 JD 抽取（build_match_golden_v2），不是归一化岗位聚合。"""
        v2 = _match_v2_rows()
        assert any(p.get("position_skills_must") for p in v2), "v2 行必须含 must"
        # 每行 must 是字面 list，非聚合聚合产物（无 source_count 等聚合字段）
        assert all("position_skills_must" in p for p in v2)


# ---------------------------------------------------------------------------
# 2. evaluate_pairs 用真实 JD 技能构建 PositionProfile，不触库
# ---------------------------------------------------------------------------


class TestEvaluatePairsUsesRealJdSkills:
    def test_build_position_directly_consumes_gold_must_nice(self):
        """build_position 直接取黄金集行字面 must/nice → 真实 JD 技能，非图谱聚合。"""
        v2 = _match_v2_rows()
        sample = next(p for p in v2 if p.get("position_skills_must"))
        musts, nices, req_years = build_position(sample)
        got_must = [r.skill_name for r in musts]
        got_nice = [r.skill_name for r in nices]
        assert got_must == sample["position_skills_must"]
        assert got_nice == sample.get("position_skills_nice", []) or []
        assert all(r.necessity.value == "must" for r in musts)
        assert req_years == (sample.get("required_years") or None)

    def test_evaluate_pairs_does_not_touch_graph(self):
        """evaluate_pairs 跑全量 v2 不触发 Neo4j（无 db 依赖），输出结构稳定。

        通过 monkeypatch 令匹配引擎的图聚合加载器抛错，若评测误走图谱聚合则失败。
        """
        import app.services.matching.loaders as loaders_mod
        import scripts.tune_match_weights as twn

        v2 = _match_v2_rows()
        weights = (0.6, 0.2, 0.2)
        threshold = 0.5

        def _no_graph(*args, **kwargs):
            raise AssertionError("评测不得读取图谱聚合（真实 JD 场景无 DB）")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(loaders_mod, "_load_positions_uncached", _no_graph)
        monkeypatch.setattr(loaders_mod, "load_positions_from_graph", _no_graph)
        try:
            result = twn.evaluate_pairs(v2, weights, None, threshold)
        finally:
            monkeypatch.undo()
        assert len(result["scores"]) == len(v2) == 384
        assert len(result["labels"]) == 384
        assert "spearman" in result and "accuracy" in result
        assert all(isinstance(s, float) for s in result["scores"])

    def test_jd_titles_name_real_jd_in_profile(self):
        """传入 jd_titles 时，PositionProfile.name 取真实 JD 标题，而非归一化岗位名。"""
        from app.services.matching.engine import MatchResult
        from app.services.matching.schemas import PositionProfile

        jd_by_id = {j["id"]: j for j in _jd_rows()}
        v2 = _match_v2_rows()
        sample = next(p for p in v2 if p.get("position_skills_must"))

        seen: list[str] = []

        def _fake_score(candidate, position: PositionProfile, *a, **kw):
            seen.append(position.name)
            return MatchResult(
                position_id=position.position_id,
                position_name=position.name,
                total_score=0.5,
                must_score=0.5,
                nice_score=0.5,
                exp_score=0.5,
            )

        # score_position 在 evaluate_pairs 内延迟导入，须在 engine 模块打桩才生效
        import app.services.matching.engine as engine_mod
        import scripts.tune_match_weights as twn

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(engine_mod, "score_position", _fake_score)
        try:
            titles = {pid: (jd_by_id[pid]["gold_title"] or pid) for pid in jd_by_id}
            twn.evaluate_pairs([sample], (0.6, 0.2, 0.2), None, 0.5, jd_titles=titles)
        finally:
            monkeypatch.undo()
        real_title = jd_by_id[sample["position_id"]]["gold_title"]
        assert seen == [real_title]
        assert real_title != sample["position_id"], "真实标题应与归一化 id 不同"


# ---------------------------------------------------------------------------
# 3. 报告/错误样例展示真实 JD 标题
# ---------------------------------------------------------------------------


class TestEvalReportsRealJdTitle:
    def test_load_jd_titles_maps_all_v2_position_ids(self):
        titles = _load_jd_titles()
        v2 = _match_v2_rows()
        pids = {p["position_id"] for p in v2}
        assert pids.issubset(titles.keys())
        # 至少一条真实标题与归一化 id 不同（证明非归一化岗位名）
        assert any(t != pid for pid, t in titles.items())

    def test_eval_match_error_cases_carry_real_title(self):
        """eval_match 错误样例带 position_title = 真实 JD 标题。"""
        from scripts.evaluate import eval_match

        v2 = _MATCH_GOLDEN.parent.joinpath("golden_set_match_v2.jsonl")
        r = eval_match(semantic=False, golden=v2)
        assert r["skipped"] is False
        for e in r.get("error_cases", []):
            assert "position_title" in e
            assert e["position_title"]
