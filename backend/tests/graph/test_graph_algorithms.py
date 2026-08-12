"""图算法单元测试（设计文档 7.1 图算法应用）。

覆盖：
- PageRank：线性/星型小图得分排序与收敛、空图/孤立节点
- Louvain：已知两簇划分、确定性、空图/单节点
- 最短路径：可达/不可达/图谱异常
- 网络加载：共现图构建、权重下限过滤、图谱不可达降级
"""

import pytest

from app.services.graph_algorithms.louvain import louvain
from app.services.graph_algorithms.network import load_skill_cooccurrence
from app.services.graph_algorithms.pagerank import pagerank
from app.services.graph_algorithms.shortest_path import shortest_path


# ============================================================
# PageRank
# ============================================================

class TestPageRank:
    def test_star_center_ranks_highest(self):
        # 星型图：中心节点被 3 个叶子指向，重要性应最高
        graph = {
            "center": {"a": 1.0, "b": 1.0, "c": 1.0},
            "a": {"center": 1.0},
            "b": {"center": 1.0},
            "c": {"center": 1.0},
        }
        scores = pagerank(graph)
        assert scores["center"] > scores["a"]
        assert scores["center"] > scores["b"]
        assert scores["center"] > scores["c"]

    def test_sum_is_one(self):
        graph = {"a": {"b": 1.0, "c": 1.0}, "b": {"a": 1.0, "c": 1.0}, "c": {"a": 1.0, "b": 1.0}}
        scores = pagerank(graph)
        assert sum(scores.values()) == pytest.approx(1.0, abs=1e-6)

    def test_empty_graph(self):
        assert pagerank({}) == {}

    def test_isolated_node(self):
        # 孤立节点按悬空处理，不应崩溃
        graph = {"a": {}, "b": {"c": 1.0}, "c": {"b": 1.0}}
        scores = pagerank(graph)
        assert set(scores) == {"a", "b", "c"}

    def test_converges(self):
        graph = {
            "a": {"b": 1.0, "c": 1.0},
            "b": {"a": 1.0},
            "c": {"a": 1.0},
        }
        scores = pagerank(graph)
        assert all(v >= 0.0 and v <= 1.0 for v in scores.values())


# ============================================================
# Louvain
# ============================================================

class TestLouvain:
    def test_two_clusters(self):
        # 两个稠密簇（A-B-C 三角形 / D-E-F 三角形），簇间仅一条弱边
        graph = {
            "A": {"B": 1.0, "C": 1.0},
            "B": {"A": 1.0, "C": 1.0},
            "C": {"A": 1.0, "B": 1.0, "D": 0.2},
            "D": {"C": 0.2, "E": 1.0, "F": 1.0},
            "E": {"D": 1.0, "F": 1.0},
            "F": {"D": 1.0, "E": 1.0},
        }
        result = louvain(graph)
        # A/B/C 应同簇，D/E/F 应同簇
        assert result["A"] == result["B"] == result["C"]
        assert result["D"] == result["E"] == result["F"]
        assert result["A"] != result["D"]

    def test_deterministic(self):
        graph = {
            "A": {"B": 1.0, "C": 1.0},
            "B": {"A": 1.0, "C": 1.0},
            "C": {"A": 1.0, "B": 1.0},
            "D": {"E": 1.0, "F": 1.0},
            "E": {"D": 1.0, "F": 1.0},
            "F": {"D": 1.0, "E": 1.0},
        }
        assert louvain(graph) == louvain(graph)

    def test_empty_graph(self):
        assert louvain({}) == {}

    def test_single_node(self):
        assert louvain({"A": {}}) == {"A": 0}

    def test_all_clustered(self):
        # 每个节点都应属于某簇（无节点丢失）
        graph = {
            "A": {"B": 1.0}, "B": {"A": 1.0, "C": 1.0}, "C": {"B": 1.0},
            "D": {"E": 1.0}, "E": {"D": 1.0},
        }
        result = louvain(graph)
        assert set(result) == set(graph)


# ============================================================
# 最短路径
# ============================================================

class _Record:
    def __init__(self, value):
        self._value = value

    def __getitem__(self, key):
        return self._value[key]


class _Result:
    def __init__(self, row):
        self._row = row

    def single(self):
        return self._row


class _PathSession:
    """可控桩：按 from/to 返回预设路径或 None。"""

    def __init__(self, paths: dict[tuple[str, str], list | None], raise_error=False):
        self._paths = paths
        self._raise_error = raise_error
        self.queries: list[str] = []

    def run(self, query, **params):
        self.queries.append(query)
        if self._raise_error:
            raise RuntimeError("graph unavailable")
        path = self._paths.get((params["from"], params["to"]))
        if path is None:
            return _Result(None)
        return _Result(_Record({"path": path}))


class TestShortestPath:
    def test_returns_path(self):
        session = _PathSession({
            ("sk_a", "sk_b"): [
                {"id": "sk_a", "name": "Python", "type": "Skill"},
                {"id": "pos_1", "name": "后端开发工程师", "type": "Position"},
                {"id": "sk_b", "name": "Django", "type": "Skill"},
            ]
        })
        path = shortest_path(session, "sk_a", "sk_b")
        assert [n["name"] for n in path] == ["Python", "后端开发工程师", "Django"]

    def test_unreachable_returns_none(self):
        session = _PathSession({})
        assert shortest_path(session, "sk_a", "sk_b") is None

    def test_graph_error_returns_none(self):
        session = _PathSession({}, raise_error=True)
        assert shortest_path(session, "sk_a", "sk_b") is None


# ============================================================
# 共现网络加载
# ============================================================

class _CoRow:
    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)


class _CoSession:
    """桩：返回预设共现行。"""

    def __init__(self, rows, raise_error=False):
        self._rows = rows
        self._raise_error = raise_error

    def run(self, query, **params):
        if self._raise_error:
            raise RuntimeError("graph unavailable")
        return self._rows


class TestCooccurrenceNetwork:
    def test_builds_undirected_weighted_graph(self):
        # P0 改造：权重 = 必要性组合因子 × 共现岗位数
        rows = [
            _CoRow({"source": "sk_a", "source_name": "Python", "target": "sk_b", "target_name": "Django", "n1": "must", "n2": "must", "co_occur_count": 3}),
            _CoRow({"source": "sk_b", "source_name": "Django", "target": "sk_c", "target_name": "PostgreSQL", "n1": "must", "n2": "nice", "co_occur_count": 4}),
            _CoRow({"source": "sk_c", "source_name": "PostgreSQL", "target": "sk_d", "target_name": "React", "n1": "nice", "n2": "nice", "co_occur_count": 10}),
        ]
        graph, names = load_skill_cooccurrence(_CoSession(rows), min_weight=0.1)
        # must-must: 1.0 × 3 = 3.0
        assert graph["sk_a"]["sk_b"] == 3.0
        assert graph["sk_b"]["sk_a"] == 3.0
        # must-nice: 0.5 × 4 = 2.0
        assert graph["sk_b"]["sk_c"] == 2.0
        # nice-nice: 0.2 × 10 = 2.0
        assert graph["sk_c"]["sk_d"] == 2.0
        assert names["sk_a"] == "Python"

    def test_default_min_weight_filters_weak_edges(self):
        # 默认 min_weight=2.0：nice-nice 弱边（0.2×count）被过滤
        rows = [
            _CoRow({"source": "sk_a", "source_name": "A", "target": "sk_b", "target_name": "B", "n1": "nice", "n2": "nice", "co_occur_count": 9}),
            _CoRow({"source": "sk_b", "source_name": "B", "target": "sk_c", "target_name": "C", "n1": "must", "n2": "must", "co_occur_count": 2}),
        ]
        graph, _ = load_skill_cooccurrence(_CoSession(rows))
        # nice-nice: 0.2×9=1.8 < 2.0 → 过滤
        assert "sk_a" not in graph or "sk_b" not in graph.get("sk_a", {})
        # must-must: 1.0×2=2.0 ≥ 2.0 → 保留
        assert graph["sk_b"]["sk_c"] == 2.0

    def test_missing_necessity_treated_as_nice(self):
        # necessity 缺失按 nice 处理：must-missing → 0.5×count
        rows = [
            _CoRow({"source": "sk_a", "source_name": "A", "target": "sk_b", "target_name": "B", "n1": "must", "n2": None, "co_occur_count": 4}),
        ]
        graph, _ = load_skill_cooccurrence(_CoSession(rows))
        assert graph["sk_a"]["sk_b"] == 2.0  # 0.5 × 4

    def test_min_weight_filters(self):
        rows = [
            _CoRow({"source": "sk_a", "source_name": "A", "target": "sk_b", "target_name": "B", "n1": "must", "n2": "must", "co_occur_count": 1}),
            _CoRow({"source": "sk_b", "source_name": "B", "target": "sk_c", "target_name": "C", "n1": "must", "n2": "must", "co_occur_count": 5}),
        ]
        graph, _ = load_skill_cooccurrence(_CoSession(rows), min_weight=3.0)
        # must-must×1 = 1.0 < 3.0 → 过滤
        assert "sk_a" not in graph or "sk_b" not in graph.get("sk_a", {})
        # must-must×5 = 5.0 ≥ 3.0 → 保留
        assert graph["sk_b"]["sk_c"] == 5.0

    def test_graph_unavailable_returns_empty(self):
        graph, names = load_skill_cooccurrence(_CoSession([], raise_error=True))
        assert graph == {}
        assert names == {}
