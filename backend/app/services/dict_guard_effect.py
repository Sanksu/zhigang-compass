"""dict-guard 人工审批副作用的统一执行与持久化标记（非原子性处理 2026-09-03）。

背景：跨 PG（提案/变更审计）与 Neo4j（图谱节点/边）+ Redis（动态词表）无分布式
事务。审批执行序（20260824 #477）已改为「先落库 PG → 副作用最后执行」根除了
"图谱先删、提案仍 pending"的半执行态。本模块进一步处理**副作用自身失败**的残余
非原子性：副作用（动态词表变更 / Neo4j 清理）不可回滚且可能抛异常（Neo4j 抖动、
动态词表读写异常等）。由调用方（admin 审批 / worker 每日巡检重试）将成功/失败
持久化到 DictProposal.effects_applied / effects_error，失败的已批准提案由每日
巡检幂等重试补齐——让"对账兜底"落到实处而非仅瞬时响应。

本模块统一承载副作用执行：
- admin 审批（api/v1/admin_routes/dict_guard.py）与
- worker 巡检重试（workers/dict_guard.py）
共用同一执行体，杜绝两处语义分叉。
"""

import logging

from app.services.extraction import dynamic_filters as dyn

logger = logging.getLogger(__name__)


def _cleanup_skill_nodes(term: str) -> int:
    """删除与 term 同名的 Skill 节点（scoped 清理，DETACH 连带 EVIDENCED_BY 等边）。"""
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        record = session.run(
            "MATCH (s:Skill {name: $term}) DETACH DELETE s RETURN count(s) AS n",
            term=term,
        ).single()
        return record["n"] if record else 0


def _cleanup_position_node(term: str) -> int:
    """删除脏岗位节点（DETACH 连带 REQUIRES 等边）。"""
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        record = session.run(
            "MATCH (p:Position {name: $term}) DETACH DELETE p RETURN count(p) AS n",
            term=term,
        ).single()
        return record["n"] if record else 0


def _cleanup_course_node(term: str) -> int:
    """删除孤立脏课程节点（DETACH 连带 LEARNABLE_VIA 等边）。"""
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        record = session.run(
            "MATCH (c:Course {name: $term}) DETACH DELETE c RETURN count(c) AS n",
            term=term,
        ).single()
        return record["n"] if record else 0


def _cleanup_course_edge(term: str) -> int:
    """删除课程脏边「技能→课程」（LEARNABLE_VIA，不删课程节点）。"""
    from app.core.database import neo4j_driver

    source, target = term.split("→", 1) if "→" in term else (term, "")
    with neo4j_driver.session() as session:
        record = session.run(
            "MATCH (s:Skill {name: $source})-[r:LEARNABLE_VIA]->(c:Course {name: $target}) "
            "DELETE r RETURN count(r) AS n",
            source=source, target=target,
        ).single()
        return record["n"] if record else 0


def cleanup_by_action(
    *, action: str, entity_type: str, term: str
) -> tuple[str, int]:
    """按提案 action/entity_type 分派图谱清理动作；返回 (kind, 受影响单元数)。

    供 remove_node / remove_edge 使用（add_stopword 等动态层动作另经
    apply_review_effect 处理；本函数专注 Neo4j 清理）。
    """
    if action == "add_stopword":
        return "blocked", _cleanup_skill_nodes(term)
    if action == "remove_node":
        if entity_type == "position":
            return "node", _cleanup_position_node(term)
        return "node", _cleanup_course_node(term)
    if action == "remove_edge":
        return "edge", _cleanup_course_edge(term)
    return "blocked", 0


def apply_review_effect(
    *,
    action: str,
    entity_type: str,
    kind: str,
    term: str,
    reason: str,
) -> dict:
    """执行一次人工审批/巡检重试的**全部**副作用（动态词表变更 + Neo4j 清理）。

    语义与 admin review 的 approve 一致：
    - add_stopword（kind=blocked）：动态 blocked 即时生效 + scoped 清理同名 Skill
    - remove_stopword：kind=blocked → 动态移除 blocked；kind=protected → 动态
      protect 静态词（受影响技能，term 为效应词，静态词本身不动）
    - protect_whitelist（kind=protected）：动态 protect 具体技能
    - remove_node/remove_edge（kind=node/edge，position/course）：图谱清理

    幂等：Neo4j MATCH…DETACH DELETE / DELETE 对已删除目标返回 0；动态词表
    add/remove 为 set 语义，重复执行无副作用——故巡检可安全重试。

    返回 impact_stats 增量（放进 changelog/proposal）。抛异常 = 副作用失败，
    由调用方决定落 effects_applied=False。
    """
    if action == "add_stopword":
        dyn.add_entry("blocked", term, reason=reason, source="dict_guard_review")
        return {"removed_nodes": _cleanup_skill_nodes(term)}
    if action == "remove_stopword":
        if kind == "blocked":
            dyn.remove_entry("blocked", term)
            return {}
        # kind == "protected"：静态停用词不动（走 git 固化），保护受影响技能穿透
        dyn.add_entry("protected", term, reason=reason, source="dict_guard_review")
        return {}
    if action == "protect_whitelist":
        dyn.add_entry("protected", term, reason=reason, source="dict_guard_review")
        return {}
    if action == "remove_node":
        removed = (
            _cleanup_position_node(term)
            if entity_type == "position"
            else _cleanup_course_node(term)
        )
        return {"removed_units": removed, "kind": "node"}
    if action == "remove_edge":
        return {"removed_units": _cleanup_course_edge(term), "kind": "edge"}
    logger.error("未知 dict-guard 副作用动作 action=%s entity_type=%s", action, entity_type)
    raise ValueError(f"未知 dict-guard 副作用动作: {action}")