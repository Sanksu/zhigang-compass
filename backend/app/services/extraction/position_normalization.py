"""统一读取 JD 快照中的规范岗位名。"""

from typing import Any

from app.services.extraction.dictionary import normalize_position_name


def _skills_from_extraction(extraction: dict[str, Any]) -> list[str]:
    skills: list[str] = []
    for skill in extraction.get("skills") or []:
        if isinstance(skill, dict) and skill.get("name"):
            skills.append(str(skill["name"]))
    return skills


def normalized_position_from_snapshot(snapshot: dict[str, Any] | None) -> str:
    """Return persisted normalized position, with a legacy snapshot fallback."""
    snapshot = snapshot or {}
    persisted = str(snapshot.get("normalized_position") or "").strip()
    if persisted:
        return persisted
    extraction = snapshot.get("extraction") or {}
    if not isinstance(extraction, dict):
        return ""
    raw_name = str(extraction.get("position_name") or "").strip()
    return normalize_position_name(raw_name, skills=_skills_from_extraction(extraction))


def persist_normalized_position(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Copy a snapshot and add its deterministic normalized position."""
    result = dict(snapshot or {})
    result["normalized_position"] = normalized_position_from_snapshot(
        {**result, "normalized_position": ""}
    )
    return result
