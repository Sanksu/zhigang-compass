"""Frozen, deterministic semantic evaluation coverage."""

from pathlib import Path

from scripts.evaluate_position_semantics import (
    _load_jsonl,
    evaluate_positions,
    evaluate_proficiency,
)

ROOT = Path(__file__).resolve().parents[2]


def test_position_normalization_frozen_samples_cover_required_routes():
    rows = _load_jsonl(ROOT / "data" / "golden_set" / "position_normalization_frozen.jsonl")

    result = evaluate_positions(rows)

    assert result["samples"] == 6
    assert result["passed"] == 6
    assert result["accuracy"] == 1.0
    assert set(result["by_category"]) == {"中文", "英文", "混合", "技术栈", "泛化路由", "拒绝"}
    assert result["failures"] == []


def test_proficiency_frozen_samples_report_legal_invalid_and_weak_results():
    rows = _load_jsonl(ROOT / "data" / "golden_set" / "proficiency_semantics_frozen.jsonl")

    result = evaluate_proficiency(rows)

    assert result["samples"] == 7
    assert result["passed"] == 7
    assert result["legal_levels"] == {"专家": 1, "初级": 2, "中级": 1, "高级": 1}
    assert result["invalid_nonempty_levels"] == 1
    assert result["weak_results"] == 4
    assert result["failures"] == []


def test_semantics_evaluator_fails_fast_for_malformed_jsonl(tmp_path):
    broken = tmp_path / "broken.jsonl"
    broken.write_text("not json\n", encoding="utf-8")

    try:
        _load_jsonl(broken)
    except ValueError as exc:
        assert "不是合法 JSON" in str(exc)
    else:
        raise AssertionError("malformed JSONL must fail")
