"""图谱查询仓储层：接收驱动 → 开 session → 委托 queries.py 会话级查询。

本模块不 import neo4j_driver（驱动由 graph.py 包装层在调用时显式传入，
graph.neo4j_driver 经测试 patch 后直达本层，见 tests/graph/test_graph_query_text.py
等对 _query_* 包装的调用路径）。每个函数只做「开 session + 转调」，Cypher
与结果处理全部收敛在 queries.py。
"""

from app.services.graph import queries


def query_panorama(driver, scope, focus, min_weight, limit) -> tuple[dict, list]:
    with driver.session() as session:
        return queries.query_panorama(session, scope, focus, min_weight, limit)


def query_skill_positions(driver, skill_id, status_filter) -> list[dict]:
    with driver.session() as session:
        return queries.query_skill_positions(session, skill_id, status_filter)


def query_fulltext_search(driver, q, type_, status_clause, offset, size) -> tuple[list[dict], int]:
    with driver.session() as session:
        return queries.query_fulltext_search(session, q, type_, status_clause, offset, size)


def query_position_skills_by_necessity(driver, id) -> dict[str, dict]:
    with driver.session() as session:
        return queries.query_position_skills_by_necessity(session, id)


def query_prereq_chain(driver, skill_name) -> list[str]:
    with driver.session() as session:
        return queries.query_prereq_chain(session, skill_name)


def query_skill_ids(driver, names) -> dict[str, str]:
    with driver.session() as session:
        return queries.query_skill_ids(session, names)


def query_position_skills(driver, id, necessity, status_filter) -> list[dict]:
    with driver.session() as session:
        return queries.query_position_skills(session, id, necessity, status_filter)


def query_all_skills(driver) -> list[tuple[str, str]]:
    with driver.session() as session:
        return queries.query_all_skills(session)


def query_skill_counts(driver, skill_id, status_filter) -> dict:
    with driver.session() as session:
        return queries.query_skill_counts(session, skill_id, status_filter)


def query_graph_counts(driver) -> dict:
    with driver.session() as session:
        return queries.query_graph_counts(session)


def query_skill_evidence(driver, skill_id) -> list[dict]:
    with driver.session() as session:
        return queries.query_skill_evidence(session, skill_id)


def query_shortest_path(driver, from_skill, to_skill, statuses) -> list | None:
    with driver.session() as session:
        return queries.query_shortest_path(session, from_skill, to_skill, statuses)


def query_view_techstack(driver, limit, status_filter) -> list:
    with driver.session() as session:
        return queries.query_view_techstack(session, limit, status_filter)


def query_view_main(driver, limit, status_filter) -> list:
    with driver.session() as session:
        return queries.query_view_main(session, limit, status_filter)


def load_skill(driver, skill_id) -> dict | None:
    with driver.session() as session:
        return queries.load_skill(session, skill_id)


def load_position(driver, id, user=None) -> dict | None:
    with driver.session() as session:
        return queries.load_position(session, id, user)
