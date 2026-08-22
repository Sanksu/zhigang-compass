"""Tests for persisted JD position normalization."""

import pytest

from app.services.extraction import position_normalization
from app.services.extraction.position_normalization import (
    POSITION_NORMALIZATION_VERSION,
    normalized_position_from_snapshot,
    persist_normalized_position,
)
from scripts.backfill_normalized_positions import _classify, _version_diff


def test_current_version_persisted_value_wins_for_existing_snapshot():
    snapshot = {
        "normalized_position": "人工审核岗位",
        "normalized_position_meta": {"version": POSITION_NORMALIZATION_VERSION},
        "extraction": {"position_name": "前端开发"},
    }
    assert normalized_position_from_snapshot(snapshot) == "人工审核岗位"


def test_legacy_persisted_value_falls_back_to_current_rules():
    snapshot = {
        "normalized_position": "旧规则岗位",
        "normalized_position_meta": {"version": "2026-08-01.1"},
        "extraction": {"position_name": "前端开发"},
    }
    assert normalized_position_from_snapshot(snapshot) == "前端开发工程师"


@pytest.mark.parametrize(
    ("snapshot", "expected_skills"),
    [
        (
            {
                "normalized_position": "旧规则岗位",
                "normalized_position_meta": {"version": "2026-08-01.1"},
                "extraction": {
                    "skills": [{"name": "React"}, {"name": "Python"}],
                    "requirements": [
                        {"skill_name": "Python"},
                        {"name": "Docker"},
                        {"skill_name": "React"},
                    ],
                },
            },
            ["React", "Python", "Docker"],
        ),
        (
            {
                "extraction": {
                    "skills": [{"name": "React"}],
                    "requirements": [
                        {"skill_name": "React"},
                        {"name": "Docker"},
                    ],
                },
            },
            ["React", "Docker"],
        ),
    ],
    ids=["stale-version", "missing-version"],
)
def test_legacy_snapshot_skill_fallback_merges_and_deduplicates_sources(
    monkeypatch, snapshot, expected_skills
):
    monkeypatch.setattr(
        position_normalization,
        "normalize_position_name",
        lambda _name, *, skills: ",".join(skills),
    )

    assert normalized_position_from_snapshot(snapshot) == ",".join(expected_skills)


def test_backfill_classifies_legacy_value_as_version_upgrade():
    snapshot = {
        "normalized_position": "前端开发工程师",
        "extraction": {"position_name": "前端开发"},
    }
    classification, old, old_version = _classify(snapshot, "前端开发工程师")

    assert (classification, old, old_version) == (
        "version_upgrade", "前端开发工程师", ""
    )


def test_backfill_version_report_includes_legacy_diff():
    assert _version_diff([
        {
            "old_normalization_version": "",
            "new_normalization_version": POSITION_NORMALIZATION_VERSION,
        }
    ]) == {f"legacy→{POSITION_NORMALIZATION_VERSION}": 1}


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
    assert persisted["normalized_position_meta"] == {"version": POSITION_NORMALIZATION_VERSION}
    assert "normalized_position" not in snapshot
    assert "normalized_position_meta" not in snapshot
