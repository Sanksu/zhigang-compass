"""岗位归一化与图谱一致性只读对账。

本工具仅执行 PostgreSQL SELECT 和 Neo4j MATCH/RETURN 查询：不提供 --fix/--apply，
不执行写入、删除或 schema 变更。用于在获准的运行环境中核对岗位归一化覆盖、
PG/Neo4j 岗位集合、REQUIRES.level 以及职业分类关系。

用法（cwd=backend；由运维在目标环境显式提供连接配置，不读取 .env）：
    POSTGRES_DSN=... NEO4J_URI=... NEO4J_USER=... NEO4J_PASSWORD=... \\
        uv run python scripts/audit_position_graph_readonly.py
    uv run python scripts/audit_position_graph_readonly.py --out reports/position_graph_audit.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.extraction.position_normalization import normalized_position_from_snapshot
from app.services.proficiency import CANONICAL_PROFICIENCY_LEVELS, normalize_proficiency_level

_DEFAULT_OUT = ROOT / "reports" / "position_graph_audit.json"

_GRAPH_POSITIONS_QUERY = """
MATCH (p:Position)
WHERE p.name IS NOT NULL AND trim(toString(p.name)) <> ''
RETURN DISTINCT p.name AS name
"""
_GRAPH_REQUIRES_QUERY = """
MATCH (p:Position)-[r:REQUIRES]->(s:Skill)
RETURN p.name AS position, s.name AS skill, r.level AS level
"""
_GRAPH_CAREER_QUERY = """
MATCH (p:Position)
OPTIONAL MATCH (p)-[:BELONGS_TO_OCCUPATION]->(o:Occupation)
RETURN p.name AS position, count(o) AS occupation_relations
"""


def _empty_reason(snapshot: dict[str, Any], normalized: str) -> str | None:
    """Explain why one PG JD cannot contribute a normalized position."""
    if normalized:
        return None
    extraction = snapshot.get("extraction")
    if not isinstance(extraction, dict):
        return "缺少 extraction"
    raw_position = extraction.get("position_name")
    if not isinstance(raw_position, str) or not raw_position.strip():
        return "缺少 extraction.position_name"
    return "归一化规则拒绝"


def reconcile(
    snapshots: list[dict[str, Any]],
    graph_positions: set[str],
    requires: list[dict[str, Any]],
    career_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a serializable audit report from already-read database data."""
    normalized_positions: set[str] = set()
    empty_reasons: Counter[str] = Counter()
    for snapshot in snapshots:
        normalized = normalized_position_from_snapshot(snapshot)
        if normalized:
            normalized_positions.add(normalized)
        else:
            empty_reasons[_empty_reason(snapshot, normalized) or "未知"] += 1

    invalid_levels: list[dict[str, Any]] = []
    level_counts: Counter[str] = Counter()
    missing_level = 0
    for edge in requires:
        level = edge.get("level")
        if not isinstance(level, str) or not level.strip():
            missing_level += 1
            continue
        normalized_level = normalize_proficiency_level(level)
        if normalized_level not in CANONICAL_PROFICIENCY_LEVELS:
            invalid_levels.append({
                "position": edge.get("position", ""),
                "skill": edge.get("skill", ""),
                "level": level,
            })
        else:
            level_counts[normalized_level] += 1

    positions_without_occupation = sorted(
        str(row.get("position"))
        for row in career_rows
        if row.get("position") and int(row.get("occupation_relations") or 0) == 0
    )
    pg_only = sorted(normalized_positions - graph_positions)
    graph_only = sorted(graph_positions - normalized_positions)
    return {
        "read_only": True,
        "normalization": {
            "pg_jd_rows": len(snapshots),
            "normalized_rows": len(snapshots) - sum(empty_reasons.values()),
            "normalized_position_count": len(normalized_positions),
            "empty_reason_counts": dict(sorted(empty_reasons.items())),
        },
        "position_set_difference": {
            "pg_only_count": len(pg_only),
            "neo4j_only_count": len(graph_only),
            "pg_only": pg_only,
            "neo4j_only": graph_only,
        },
        "requires_level": {
            "edge_count": len(requires),
            "filled_count": len(requires) - missing_level,
            "missing_count": missing_level,
            "legal_level_counts": dict(sorted(level_counts.items())),
            "invalid_count": len(invalid_levels),
            "invalid": invalid_levels,
        },
        "occupation_relation": {
            "position_count": len(career_rows),
            "covered_count": len(career_rows) - len(positions_without_occupation),
            "uncovered_count": len(positions_without_occupation),
            "uncovered_positions": positions_without_occupation,
        },
    }


def _required_env(name: str) -> str:
    """Read an explicitly supplied connection setting without reading dotenv files."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}；本脚本不会读取 .env 文件")
    return value


async def _load_pg_snapshots() -> list[dict[str, Any]]:
    """Read JD snapshots only, using a caller-supplied DSN and no dotenv loader."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_required_env("POSTGRES_DSN"))
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT snapshot FROM jd_raw"))
            snapshots = result.scalars().all()
    finally:
        await engine.dispose()
    return [snapshot for snapshot in snapshots if isinstance(snapshot, dict)]


def _load_graph_data() -> tuple[set[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read graph data using caller-supplied credentials and MATCH/RETURN-only Cypher."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        _required_env("NEO4J_URI"),
        auth=(_required_env("NEO4J_USER"), _required_env("NEO4J_PASSWORD")),
    )
    try:
        with driver.session() as session:
            positions = {
                str(row["name"])
                for row in session.run(_GRAPH_POSITIONS_QUERY).data()
                if row.get("name")
            }
            requires = session.run(_GRAPH_REQUIRES_QUERY).data()
            career_rows = session.run(_GRAPH_CAREER_QUERY).data()
    finally:
        driver.close()
    return positions, requires, career_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="岗位归一化与图谱一致性只读对账")
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT, help="JSON 对账报告路径")
    args = parser.parse_args()

    snapshots = asyncio.run(_load_pg_snapshots())
    graph_positions, requires, career_rows = _load_graph_data()
    report = reconcile(snapshots, graph_positions, requires, career_rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"PG 规范岗位: {report['normalization']['normalized_position_count']}")
    print(f"归一化空值原因: {report['normalization']['empty_reason_counts']}")
    print(
        "岗位集合差: "
        f"PG-only={report['position_set_difference']['pg_only_count']} / "
        f"Neo4j-only={report['position_set_difference']['neo4j_only_count']}"
    )
    levels = report["requires_level"]
    print(
        f"REQUIRES.level: filled={levels['filled_count']}/{levels['edge_count']} "
        f"invalid={levels['invalid_count']}"
    )
    occupation = report["occupation_relation"]
    print(f"职业关系: covered={occupation['covered_count']}/{occupation['position_count']}")
    print(f"报告: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
