"""Tests for persisted JD position normalization."""

from app.services.extraction.position_normalization import (
    normalized_position_from_snapshot,
    persist_normalized_position,
)


def test_persisted_value_wins_for_existing_snapshot():
    snapshot = {
        "normalized_position": "人工审核岗位",
        "extraction": {"position_name": "前端开发"},
    }
    assert normalized_position_from_snapshot(snapshot) == "人工审核岗位"


def test_legacy_snapshot_falls_back_with_skill_routing():
    snapshot = {
        "extraction": {
            "position_name": "算法工程师",
            "skills": [{"name": "计算机视觉"}, {"name": "PyTorch"}],
        }
    }
    assert normalized_position_from_snapshot(snapshot)


def test_persist_keeps_raw_extraction_position():
    snapshot = {
        "extraction": {
            "position_name": "前端开发",
            "skills": [{"name": "React"}],
        }
    }
    persisted = persist_normalized_position(snapshot)
    assert persisted["extraction"]["position_name"] == "前端开发"
    assert persisted["normalized_position"]
    assert "normalized_position" not in snapshot
