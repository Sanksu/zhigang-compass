"""版本化的 JD 快照岗位归一化读写单一入口。"""

from typing import Any

from app.services.extraction.dictionary import normalize_position_name


# 每次规则语义变更时提升该版本。快照消费者只信任同版本的持久化结果；
# 旧版或无版本快照统一通过受控规则回退，避免图谱、聚合和发现各自采用不同口径。
POSITION_NORMALIZATION_VERSION = "2026-08-23.1"
NORMALIZED_POSITION_META_KEY = "normalized_position_meta"


def _skills_from_extraction(extraction: dict[str, Any]) -> list[str]:
    skills: list[str] = []
    seen: set[str] = set()

    def add_skill(value: Any) -> None:
        name = str(value or "").strip()
        if name and name not in seen:
            seen.add(name)
            skills.append(name)

    for skill in extraction.get("skills") or []:
        if isinstance(skill, dict):
            add_skill(skill.get("name"))
    for requirement in extraction.get("requirements") or []:
        if isinstance(requirement, dict):
            add_skill(requirement.get("skill_name") or requirement.get("name"))
    return skills


def normalization_version(snapshot: dict[str, Any] | None) -> str:
    """Return the persisted normalization version, or an empty string for legacy data."""
    meta = (snapshot or {}).get(NORMALIZED_POSITION_META_KEY)
    return str(meta.get("version") or "").strip() if isinstance(meta, dict) else ""


def has_current_normalized_position(snapshot: dict[str, Any] | None) -> bool:
    """Whether a snapshot contains a nonempty normalized position from this rules version."""
    persisted = str((snapshot or {}).get("normalized_position") or "").strip()
    return bool(persisted) and normalization_version(snapshot) == POSITION_NORMALIZATION_VERSION


def _normalize_from_extraction(snapshot: dict[str, Any]) -> str:
    extraction = snapshot.get("extraction") or {}
    if not isinstance(extraction, dict):
        return ""
    raw_name = str(extraction.get("position_name") or "").strip()
    return normalize_position_name(raw_name, skills=_skills_from_extraction(extraction))


def normalized_position_from_snapshot(snapshot: dict[str, Any] | None) -> str:
    """Read a valid persisted name, otherwise recompute it using the current rules.

    Legacy and stale snapshots deliberately do not trust their old value. This is the
    shared read path for graph import, aggregation, cross-validation, and discovery.
    """
    snapshot = snapshot or {}
    if has_current_normalized_position(snapshot):
        return str(snapshot["normalized_position"]).strip()
    return _normalize_from_extraction(snapshot)


def candidate_position_rename_plan(
    candidate_names: set[str], snapshots: list[dict[str, Any] | None],
) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Return safe legacy candidate renames and explicitly ambiguous mappings.

    Discovery candidates are keyed by normalized position name.  A legacy name
    may split into several current names, or multiple legacy names may converge
    on one current name.  Both cases are ambiguous: only a one-to-one mapping
    whose target is absent from the initial candidate keys can be renamed.
    """
    targets_by_old: dict[str, set[str]] = {}
    for snapshot in snapshots:
        snapshot = snapshot or {}
        old_name = str(snapshot.get("normalized_position") or "").strip()
        if old_name not in candidate_names:
            continue
        new_name = normalized_position_from_snapshot(snapshot)
        if new_name and new_name != old_name:
            targets_by_old.setdefault(old_name, set()).add(new_name)

    ambiguous = {
        old_name: targets
        for old_name, targets in targets_by_old.items()
        if len(targets) > 1
    }
    old_names_by_target: dict[str, set[str]] = {}
    for old_name, targets in targets_by_old.items():
        if len(targets) == 1:
            target = next(iter(targets))
            old_names_by_target.setdefault(target, set()).add(old_name)

    renames: dict[str, str] = {}
    for target, old_names in old_names_by_target.items():
        if len(old_names) == 1 and target not in candidate_names:
            old_name = next(iter(old_names))
            if old_name not in ambiguous:
                renames[old_name] = target
            continue
        for old_name in old_names:
            ambiguous.setdefault(old_name, set()).add(target)
    return renames, ambiguous


def persist_normalized_position(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Copy a snapshot and persist its current deterministic value and version metadata."""
    result = dict(snapshot or {})
    result["normalized_position"] = _normalize_from_extraction(result)
    result[NORMALIZED_POSITION_META_KEY] = {"version": POSITION_NORMALIZATION_VERSION}
    return result
