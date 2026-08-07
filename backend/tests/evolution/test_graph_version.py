"""图谱版本快照管理测试（设计文档 §7.1 版本管理）。

覆盖纯函数逻辑：节点 diff 计算与导出过滤（真实 Neo4j/DB 写库由集成测试覆盖）。
"""

import pytest

from app.services.evolution.graph_version import (
    GraphVersionManager,
    _LABEL_TO_TYPE,
    _SKIP_LABELS,
)


class _FakeGraphVersion:
    """最小 GraphVersion 替身（仅暴露 diff 所需的 snapshot_json 属性）。"""

    def __init__(self, snapshot_json: dict):
        self.snapshot_json = snapshot_json


def test_diff_node_sets_without_previous():
    """无上一版本：全部节点计入 added，无 removed/changed。"""
    added, removed, changed = GraphVersionManager._diff_node_sets(None, [
        {"id": "pos_1"}, {"id": "sk_1"},
    ])
    assert (added, removed, changed) == (2, 0, 0)


def test_diff_node_sets_with_growth():
    """有上一版本：新增节点计入 added，共有且属性不变的节点不计入 changed。"""
    prev = _FakeGraphVersion({"nodes": [{"id": "pos_1", "name": "后端开发工程师", "type": "position"},
                                        {"id": "sk_1", "name": "Python", "type": "skill"}]})
    cur = [{"id": "pos_1", "name": "后端开发工程师", "type": "position"},
           {"id": "sk_1", "name": "Python", "type": "skill"},
           {"id": "sk_2", "name": "Go", "type": "skill"}]
    added, removed, changed = GraphVersionManager._diff_node_sets(prev, cur)
    assert (added, removed, changed) == (1, 0, 0)


def test_diff_node_sets_with_shrink():
    """节点减少：消失节点计入 removed。"""
    prev = _FakeGraphVersion({"nodes": [{"id": "pos_1", "name": "后端开发工程师", "type": "position"},
                                        {"id": "sk_1", "name": "Python", "type": "skill"},
                                        {"id": "sk_2", "name": "Go", "type": "skill"}]})
    cur = [{"id": "pos_1", "name": "后端开发工程师", "type": "position"},
           {"id": "sk_1", "name": "Python", "type": "skill"}]
    added, removed, changed = GraphVersionManager._diff_node_sets(prev, cur)
    assert (added, removed, changed) == (0, 1, 0)


def test_diff_node_sets_counts_attribute_changes():
    """共有节点中 name/type 变化的计入 changed（非共有数）。"""
    prev = _FakeGraphVersion({"nodes": [{"id": "pos_1", "name": "后端开发工程师", "type": "position"},
                                        {"id": "sk_1", "name": "Python", "type": "skill"}]})
    cur = [{"id": "pos_1", "name": "后端开发工程师", "type": "position"},
           {"id": "sk_1", "name": "TypeScript", "type": "skill"}]  # name 变化
    added, removed, changed = GraphVersionManager._diff_node_sets(prev, cur)
    assert (added, removed, changed) == (0, 0, 1)


def test_label_type_mapping_covers_business_entities():
    """业务实体标签均映射到快照 type，内部标签被排除。"""
    for label in ("Position", "Skill", "Evidence", "Course", "Tool"):
        assert label in _LABEL_TO_TYPE
    assert "Counter" in _SKIP_LABELS


def test_snapshot_graph_registered_as_arq_task():
    """snapshot_graph 已在 WorkerSettings 注册（入队后有 worker 消费）。"""
    from app.workers import tasks as t

    assert t.snapshot_graph in t.WorkerSettings.functions
