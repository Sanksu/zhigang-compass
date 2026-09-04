"""技能关联岗位动态变迁桑基图数据构建（_build_skill_flow）纯函数测试。

权重口径：节点 freq / 连线 value = 岗位当期 REQUIRES 出度（当期要求技能总数）；
排序 = (-出度, 岗位名)；技能无关联岗位的期次整期剔除。
"""
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.api.v1.evolution import _build_skill_flow


def _snapshot(version: str, created: str, skill_name: str, pos_deg: dict[str, int]) -> SimpleNamespace:
    """构造 GraphVersion 替身：pos_deg = {岗位名: 该岗位 REQUIRES 出度}。"""
    nodes = [
        {"id": "sk_py", "name": skill_name, "type": "skill"},
        *[{"id": f"pos_{p}", "name": p, "type": "position"} for p in pos_deg],
    ]
    edges: list[dict] = []
    for p, n in pos_deg.items():
        edges.extend({"source": f"pos_{p}", "target": "sk_py", "relation": "REQUIRES"} for _ in range(n))
    return SimpleNamespace(
        id=version,
        created_at=datetime.fromisoformat(created),
        snapshot_json={"nodes": nodes, "edges": edges},
    )


def test_flow_links_same_position_across_periods():
    snaps = [
        _snapshot("v1", "2026-08-01T05:00:00+08:00", "Python", {"后端开发": 10, "数据分析": 5}),
        _snapshot("v2", "2026-08-02T05:00:00+08:00", "Python", {"后端开发": 12, "算法工程师": 4}),
    ]
    data = _build_skill_flow(snaps, "sk_py", top_n=8)
    assert data["skill_name"] == "Python"
    assert data["periods"] == ["2026-08-01", "2026-08-02"]
    assert data["totals"] == [2, 2]
    # 后端开发两期均在 Top → 相邻期连线（值=左侧期次出度）；数据分析离开、算法工程师新进入（无连线）
    assert {"source": "pos_后端开发::0", "target": "pos_后端开发::1", "value": 10} in data["links"]
    assert not any("算法工程师::0" in l["source"] for l in data["links"])
    assert not any("数据分析::1" in l["target"] for l in data["links"])


def test_flow_rank_by_degree_then_name():
    """同权平局按岗位名升序，高出度在前——替换旧行为「边序任意 Top-N」。"""
    snaps = [
        _snapshot("v1", "2026-08-01T05:00:00+08:00", "Python",
                  {"B岗位": 3, "A岗位": 3, "C岗位": 9}),
    ]
    data = _build_skill_flow(snaps, "sk_py", top_n=8)
    assert [(n["name"], n["freq"]) for n in data["nodes"]] == [
        ("C岗位", 9), ("A岗位", 3), ("B岗位", 3),
    ]


def test_flow_top_n_truncates_per_period():
    snaps = [
        _snapshot("v1", "2026-08-01T05:00:00+08:00", "Python", {f"岗{c}": 10 - c for c in range(5)}),
        _snapshot("v2", "2026-08-02T05:00:00+08:00", "Python", {f"岗{c}": 10 - c for c in range(5)}),
    ]
    data = _build_skill_flow(snaps, "sk_py", top_n=3)
    per_period = [n for n in data["nodes"] if n["period_index"] == 0]
    assert len(per_period) == 3
    # 出度最低的岗3/岗4 被截断
    assert all(n["name"] in {"岗0", "岗1", "岗2"} for n in per_period)
    assert data["totals"] == [5, 5]  # Top-N 之外的关联岗位仍计入总数


def test_flow_truncated_position_not_linked():
    # 岗 A 两期都在但第二期跌出 Top-N → 不产生连线
    snaps = [
        _snapshot("v1", "2026-08-01T05:00:00+08:00", "Python",
                  {"A": 10, "B": 9, "C": 8, "D": 7}),
        _snapshot("v2", "2026-08-02T05:00:00+08:00", "Python",
                  {"B": 20, "C": 19, "D": 18}),
    ]
    data = _build_skill_flow(snaps, "sk_py", top_n=3)
    # v1 Top3 = A/B/C（A=10 入列），v2 Top3 = B/C/D → A 无 v2 节点，无 A 连线
    assert not any(n["id"].startswith("pos_A::1") for n in data["nodes"])
    assert not any(l["source"] == "pos_A::0" for l in data["links"])


def test_flow_empty_periods_dropped():
    """技能无关联岗位的期次整期剔除（早期稀疏快照不产生空列）。"""
    snaps = [
        _snapshot("v1", "2026-08-01T05:00:00+08:00", "Python", {}),
        _snapshot("v2", "2026-08-02T05:00:00+08:00", "Python", {"后端开发": 5}),
        _snapshot("v3", "2026-08-03T05:00:00+08:00", "Python", {}),
        _snapshot("v4", "2026-08-04T05:00:00+08:00", "Python", {"后端开发": 6, "数据分析师": 2}),
    ]
    data = _build_skill_flow(snaps, "sk_py", top_n=8)
    assert data["periods"] == ["2026-08-02", "2026-08-04"]
    assert data["totals"] == [1, 2]
    # 重排后 period_index 连续，跨期连线按新序号
    assert {n["period_index"] for n in data["nodes"]} == {0, 1}
    assert {"source": "pos_后端开发::0", "target": "pos_后端开发::1", "value": 5} in data["links"]


def test_flow_missing_skill_returns_empty_graph():
    snaps = [_snapshot("v1", "2026-08-01T05:00:00+08:00", "Python", {})]
    data = _build_skill_flow(snaps, "sk_py", top_n=8)
    assert data["nodes"] == []
    assert data["links"] == []
    assert data["periods"] == []
    assert data["totals"] == []
    assert data["skill_name"] == "Python"


def test_flow_ignores_non_requires_edges():
    """P1-2 回归：BELONGS_TO/PREREQUISITE_OF 等技能→技能边不得以「岗位」身份入列。"""
    snap = _snapshot("v1", "2026-08-01T05:00:00+08:00", "Python", {"后端开发": 10})
    snap.snapshot_json["edges"].extend([
        {"source": "sk_ml", "target": "sk_py", "relation": "PREREQUISITE_OF"},
        {"source": "sk_py", "target": "pos_后端开发", "relation": "BELONGS_TO"},
    ])
    data = _build_skill_flow([snap], "sk_py", top_n=8)
    names = {n["name"] for n in data["nodes"]}
    assert names == {"后端开发"}  # sk_ml 不入岗位列；BELONGS_TO 不增出度
    assert data["nodes"][0]["freq"] == 10


def test_flow_legacy_snapshot_without_relation_keeps_all_edges():
    """旧快照（边无 relation 字段）保持历史口径：岗位→技能边全部计入出度。"""
    snap = _snapshot("v1", "2026-08-01T05:00:00+08:00", "Python", {"后端开发": 3})
    for e in snap.snapshot_json["edges"]:
        del e["relation"]
    data = _build_skill_flow([snap], "sk_py", top_n=8)
    assert data["nodes"][0]["freq"] == 3


def test_flow_legacy_skill_source_edges_not_counted():
    """旧快照无 relation 时，技能→技能边靠 type=position 过滤不得虚增岗位出度（P1-2 同根）。"""
    snap = _snapshot("v1", "2026-08-01T05:00:00+08:00", "Python", {"后端开发": 3})
    snap.snapshot_json["edges"].append({"source": "sk_ml", "target": "sk_py"})
    data = _build_skill_flow([snap], "sk_py", top_n=8)
    assert [n["name"] for n in data["nodes"]] == ["后端开发"]
    assert data["nodes"][0]["freq"] == 3
