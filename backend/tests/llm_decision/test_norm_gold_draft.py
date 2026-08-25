"""DRAFT 独立集 + position 归一评分器测试。

覆盖两部分：
1. **DRAFT 文件合法性**：`position_normalization_draft.jsonl` 与
   `skill_normalization_draft.jsonl` 能正确加载、非空、必需字段齐备、
   `annotation_status`/`needs_human` 语义正确、机器建议与 gold 关系清晰。
2. **eval_position_normalization**：对 tiny fixture 用 mock LLM 跑，
   返回预期的指标键（canonical_accuracy / is_new_accuracy /
   keep_original_accuracy / gate_blocked_count / error_merge_count）。

注意：这些是 DRAFT（机器建议，`annotation_status="draft_auto"`），**不是**人类金标准。
本测试只验证结构/装配正确，不验证任何"人类标签"的正确性。
"""

import json
import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_DIR))

from scripts import eval_llm_driven as ev  # noqa: E402

_GOLD_DIR = _BACKEND_DIR / "data" / "golden_set" / "llm_driven"

_POS_REQUIRED = {
    "id", "raw_title", "source", "skills", "candidates",
    "gold_canonical", "gold_is_new", "gold_keep_original", "slice",
    "source_note", "gold_title_ref",
    "suggested_canonical", "suggested_is_new", "suggested_keep_original",
    "suggested_via", "annotation_status", "needs_human",
}
_SK_REQUIRED = {
    "id", "variant", "gold_action", "gold_standard", "gold_keep", "slice",
    "source_note", "candidates",
    "suggested_action", "suggested_standard", "suggested_via",
    "annotation_status", "needs_human",
}


def _load(fname: str) -> list[dict]:
    path = _GOLD_DIR / fname
    assert path.exists(), f"缺少 DRAFT 文件: {path}"
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestPositionDraftFile:
    def test_loads_and_nonempty(self):
        rows = _load("position_normalization_draft.jsonl")
        assert len(rows) >= 60  # 目标 ~60-80

    def test_all_required_fields_present(self):
        rows = _load("position_normalization_draft.jsonl")
        for row in rows:
            missing = _POS_REQUIRED - set(row.keys())
            assert not missing, f"pos 行缺字段 {missing}"

    def test_annotation_status_is_draft_auto(self):
        rows = _load("position_normalization_draft.jsonl")
        assert all(r["annotation_status"] == "draft_auto" for r in rows)

    def test_needs_human_flag_layer(self):
        """08-25 自动回填后：approval 分两层——authoritative(False) 权威已回填 + needs_human(True) 待人工裁决。

        互斥且与 resolution 一致：needs_human=False ⇔ resolution=authoritative（非独立人工，需人工转正才置 human）。
        """
        rows = _load("position_normalization_draft.jsonl")
        authoritative = [r for r in rows if r["resolution"] == "authoritative"]
        needs = [r for r in rows if r["resolution"] == "needs_human"]
        assert authoritative and needs, "应同时存在权威回填与待裁决两类"
        for r in rows:
            assert r["needs_human"] is (r["resolution"] == "needs_human"), r["id"]
        # authoritative 项均已定案（canonical 非空、is_new/keep_original=false）
        for r in authoritative:
            assert r["gold_canonical"], r["id"]
            assert r["gold_is_new"] is False and r["gold_keep_original"] is False, r["id"]

    def test_canonical_consistency(self):
        """gold_canonical 为空的仅当 keep_original=true；否则非空。"""
        rows = _load("position_normalization_draft.jsonl")
        for r in rows:
            if r["gold_keep_original"]:
                assert r["gold_canonical"] == "", r["raw_title"]
            else:
                assert r["gold_canonical"], r["raw_title"]

    def test_suggested_mirrors_gold(self):
        """机器建议（suggested_*）等于 gold_* 初值，明确标注机器产出来源。"""
        rows = _load("position_normalization_draft.jsonl")
        for r in rows:
            assert r["suggested_canonical"] == r["gold_canonical"]
            assert r["suggested_is_new"] == r["gold_is_new"]
            assert r["suggested_keep_original"] == r["gold_keep_original"]
            assert r["suggested_via"]


class TestSkillDraftFile:
    def test_loads_and_nonempty(self):
        rows = _load("skill_normalization_draft.jsonl")
        assert len(rows) >= 30

    def test_all_required_fields_present(self):
        rows = _load("skill_normalization_draft.jsonl")
        for row in rows:
            missing = _SK_REQUIRED - set(row.keys())
            assert not missing, f"skill 行缺字段 {missing}"

    def test_annotation_status_is_draft_auto(self):
        rows = _load("skill_normalization_draft.jsonl")
        assert all(r["annotation_status"] == "draft_auto" for r in rows)

    def test_needs_human_flag_layer(self):
        """08-25 自动回填后：resolved authoritative + needs_human 待裁决两层互斥。"""
        rows = _load("skill_normalization_draft.jsonl")
        authoritative = [r for r in rows if r["resolution"] == "authoritative"]
        needs = [r for r in rows if r["resolution"] == "needs_human"]
        assert authoritative and needs, "应同时存在权威回填与待裁决两类"
        for r in rows:
            assert r["needs_human"] is (r["resolution"] == "needs_human"), r["id"]
        # authoritative 项均定案：merge 有 standard，keep 的 standard==variant
        for r in authoritative:
            assert r["gold_action"] in ("merge", "keep"), r["id"]
            if r["gold_action"] == "merge":
                assert r["gold_standard"], r["id"]
            else:
                assert r["gold_standard"] == r["variant"], r["id"]
        """gold_action ∈ {merge, keep, noise}；keep 时 standard==variant。"""
        rows = _load("skill_normalization_draft.jsonl")
        for r in rows:
            assert r["gold_action"] in ("merge", "keep", "noise")
            if r["gold_action"] == "keep":
                assert r["gold_standard"] == r["variant"], r["variant"]
            if r["gold_action"] == "merge":
                assert r["gold_standard"], r["variant"]

    def test_merge_target_in_candidates(self):
        """merge 行目标标准名必须在候选清单内（与生产 alias-prepend 一致）。"""
        rows = _load("skill_normalization_draft.jsonl")
        for r in rows:
            if r["gold_action"] == "merge":
                assert r["gold_standard"] in r["candidates"], r["variant"]

    def test_slice_coverage(self):
        """抽样覆盖人工裁决重点切片（保证非纯别名派生的独立性）。"""
        rows = _load("skill_normalization_draft.jsonl")
        slices = {r["slice"] for r in rows}
        assert "near_synonym" in slices
        assert "same_initial" in slices
        assert "short_ascii" in slices
        assert "version_variant" in slices
        assert "cjk_abbr" in slices


class TestEvalPositionNormalization:
    @pytest.fixture
    def fixture_rows(self):
        # 3 行 tiny fixture：一行需归并、一行 keep_original、一行 gate 拦截（空 canonical）
        return [
            {
                "raw_title": "大数据平台开发工程师(J10846)",
                "source": "zhilian", "skills": ["Java", "Spark"],
                "candidates": ["大数据开发工程师", "前端开发工程师"],
                "gold_canonical": "大数据开发工程师",
                "gold_is_new": False, "gold_keep_original": False,
                "slice": "mixed", "annotation_status": "draft_auto",
            },
            {
                "raw_title": "单片机工程师",
                "source": "zhilian", "skills": [],
                "candidates": ["后端开发工程师", "嵌入式开发工程师"],
                "gold_canonical": "", "gold_is_new": False,
                "gold_keep_original": True, "slice": "reject",
                "annotation_status": "draft_auto",
            },
            {
                "raw_title": "前端开发工程师",
                "source": "zhilian", "skills": ["React"],
                "candidates": ["前端开发工程师", "测试开发工程师"],
                "gold_canonical": "前端开发工程师",
                "gold_is_new": False, "gold_keep_original": False,
                "slice": "cjk", "annotation_status": "human",
            },
        ]

    def test_returns_expected_keys(self, fixture_rows):
        class _MockLLM:
            def extract_structured(self, prompt, model, **kwargs):
                from app.services.llm_decision.position_name import PositionNameDecision
                # 一律 keep_original 保守决策，过 gate，便于断言结构而非数值
                return PositionNameDecision(
                    canonical_name="", is_new=False, keep_original=True, confidence=0.9,
                )

        import asyncio

        res = asyncio.run(ev.eval_position_normalization(fixture_rows, _MockLLM()))
        assert res["task"] == "position_normalization"
        assert res["total"] == 3
        for key in (
            "canonical_accuracy", "is_new_accuracy", "keep_original_accuracy",
            "gate_blocked_count", "error_merge_count", "llm_failed",
            "human_confirmed_rows", "results",
        ):
            assert key in res, f"缺少指标键 {key}"

    def test_human_row_counted(self, fixture_rows):
        class _MockLLM:
            def extract_structured(self, prompt, model, **kwargs):
                from app.services.llm_decision.position_name import PositionNameDecision
                return PositionNameDecision(
                    canonical_name="前端开发工程师", is_new=False,
                    keep_original=False, confidence=0.9,
                )

        import asyncio

        res = asyncio.run(ev.eval_position_normalization(fixture_rows, _MockLLM()))
        assert res["human_confirmed_rows"] == 1  # 仅 annotation_status=human 的那行

    def test_gate_block_counts(self, fixture_rows):
        """gate 拦截（空 canonical / 非 keep 且非候选）计为 blocked 而非错误。"""
        class _MockLLM:
            def extract_structured(self, prompt, model, **kwargs):
                from app.services.llm_decision.position_name import PositionNameDecision
                # 非 keep 但 canonical 不在候选 → gate 拦截
                return PositionNameDecision(
                    canonical_name="量子烹饪架构师", is_new=False,
                    keep_original=False, confidence=0.9,
                )

        import asyncio

        res = asyncio.run(ev.eval_position_normalization(fixture_rows, _MockLLM()))
        assert res["gate_blocked_count"] >= 1

    def test_task_routed_to_draft_file(self):
        assert ev._get_file_for("position") == "position_normalization_draft.jsonl"
