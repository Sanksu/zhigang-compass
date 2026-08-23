"""技能关系建边（设计文档 §5.1 九类关系补齐）。

将人工维护的字典数据落地为 Neo4j 边，幂等（MERGE）可安全重跑：
- PREREQUISITE_OF：先修字典（configs/skill_prerequisites.yaml）→ `(先修Skill)-[:PREREQUISITE_OF]->(目标Skill)`
- BELONGS_TO：父子技能字典（configs/skill_relations.yaml）→ `(Skill)-[:BELONGS_TO]->(Skill)`（如 Spring Boot → Java）
- ALTERNATIVE_OF：可替代技能字典（configs/skill_relations.yaml）→ `(Skill)-[:ALTERNATIVE_OF]->(Skill)`（如 React ↔ Vue，双向建边）

仅对图谱中已存在的 Skill 节点建边（字典条目对应的技能不在图谱时跳过，
避免凭空创建无证据支撑的技能节点）。

用法：
    from app.services.kg.skill_relations import sync_skill_relations
    with neo4j_driver.session() as session:
        result = sync_skill_relations(session)
"""

from functools import lru_cache
from pathlib import Path

import yaml
from neo4j import Session

# 关系类型名
REL_PREREQUISITE = "PREREQUISITE_OF"
REL_BELONGS = "BELONGS_TO"
REL_ALTERNATIVE = "ALTERNATIVE_OF"

# 字典文件路径（相对 backend 根目录）
_CONFIG_PREREQ = "configs/skill_prerequisites.yaml"
_CONFIG_RELATIONS = "configs/skill_relations.yaml"


@lru_cache(maxsize=1)
def _load_yaml(rel_path: str) -> dict:
    """加载 yaml 字典（进程内缓存，配置变更需重启生效）。"""
    path = Path(__file__).resolve().parents[3] / rel_path
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _existing_skills(session: Session) -> set[str]:
    """图谱中已存在的全部 Skill.name。"""
    rows = session.run("MATCH (s:Skill) RETURN s.name AS name").data()
    return {r["name"] for r in rows if r.get("name")}


def sync_skill_relations(session: Session, dry_run: bool = False) -> dict:
    """按字典将技能关系落地为 Neo4j 边（幂等 MERGE）。

    Args:
        session: Neo4j Session
        dry_run: True 只统计不写图谱

    Returns:
        {"prerequisite": n, "belongs_to": n, "alternative_of": n, "skipped": n}
    """
    prereq_cfg = _load_yaml(_CONFIG_PREREQ)
    relation_cfg = _load_yaml(_CONFIG_RELATIONS)
    existing = _existing_skills(session) if not dry_run else set()
    stats = {"prerequisite": 0, "belongs_to": 0, "alternative_of": 0, "skipped": 0}

    def in_graph(name: str) -> bool:
        return dry_run or name in existing

    # PREREQUISITE_OF：先修字典（先修技能 → 目标技能，P 是 X 的先修 ⇒ P→X）
    for name, entry in ((prereq_cfg.get("skills") or {}).items()):
        if not in_graph(name):
            stats["skipped"] += 1
            continue
        for pre in (entry.get("prerequisites") or []):
            if in_graph(pre):
                stats["prerequisite"] += 1
                if not dry_run:
                    session.run(
                        f"MATCH (a:Skill {{name: $a}}), (b:Skill {{name: $b}}) "
                        f"MERGE (a)-[:{REL_PREREQUISITE}]->(b)",
                        a=pre, b=name,
                    )

    # BELONGS_TO / ALTERNATIVE_OF：技能关系字典
    for name, entry in ((relation_cfg.get("skills") or {}).items()):
        if not in_graph(name):
            stats["skipped"] += 1
            continue
        # BELONGS_TO：父子技能（子 → 父）
        for parent in (entry.get("parent") or []):
            if in_graph(parent):
                stats["belongs_to"] += 1
                if not dry_run:
                    session.run(
                        f"MATCH (a:Skill {{name: $a}}), (b:Skill {{name: $b}}) "
                        f"MERGE (a)-[:{REL_BELONGS}]->(b)",
                        a=name, b=parent,
                    )
        # ALTERNATIVE_OF：可替代技能（双向）
        for alt in (entry.get("alternatives") or []):
            if in_graph(alt):
                stats["alternative_of"] += 1
                if not dry_run:
                    session.run(
                        f"MATCH (a:Skill {{name: $a}}), (b:Skill {{name: $b}}) "
                        f"MERGE (a)-[:{REL_ALTERNATIVE}]->(b)",
                        a=name, b=alt,
                    )
                    session.run(
                        f"MATCH (a:Skill {{name: $a}}), (b:Skill {{name: $b}}) "
                        f"MERGE (b)-[:{REL_ALTERNATIVE}]->(a)",
                        a=name, b=alt,
                    )
    return stats


def graph_prerequisite_chain(session: Session, skill_name: str) -> list[str]:
    """沿 PREREQUISITE_OF 入边展开先修链（拓扑序，先修在前，不含目标本身）。

    图谱未建边时返回空列表，由调用方回退先修字典。
    """
    chain: list[str] = []
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        rows = session.run(
            "MATCH (p:Skill)-[:PREREQUISITE_OF]->(s:Skill {name: $name}) RETURN p.name AS name",
            name=name,
        ).data()
        for r in rows:
            if r.get("name"):
                visit(r["name"])
        if name != skill_name:
            chain.append(name)

    visit(skill_name)
    return chain


def _import_skill_relations() -> None:
    """兼容独立脚本入口：python -m app.services.kg.skill_relations。"""
    import sys

    from app.core.database import neo4j_driver

    dry_run = "--dry-run" in sys.argv
    with neo4j_driver.session() as session:
        stats = sync_skill_relations(session, dry_run=dry_run)
    print(f"技能关系同步{'（dry-run）' if dry_run else '完成'}: {stats}")


if __name__ == "__main__":
    _import_skill_relations()
