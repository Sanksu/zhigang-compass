"""EVOLVED_FROM 演化关系推导测试（evolved_from.py）。

覆盖：命名包含/共享片段判定、快照解析、derive_evolved_from 的
版本不足/无变化/rename/split/dry_run 分支。DB 与 Neo4j 均 mock，
不依赖真实服务。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace


from app.services.evolution.evolved_from import (
    _name_containment,
    _position_nodes,
    _shared_segments,
    derive_evolved_from,
)


class _Result:
    """scalars() 返回桩：包装列表，暴露 .all()（对齐 SQLAlchemy Result API）。"""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def _version(vid: int, nodes: list[dict]) -> SimpleNamespace:
    """构造 GraphVersion 桩（仅含 derive 用到的字段）。"""
    return SimpleNamespace(
        id=f"v{vid}",
        snapshot_json={"nodes": nodes},
        created_at=vid,
    )


def _pos(nid: str, name: str) -> dict:
    return {"id": nid, "name": name, "type": "position"}


# ── _name_containment ──────────────────────────────────────────────

def test_name_containment_rename_signal():
    """新名完整包含旧名（连续子串）且长度差 ≥ 2 → rename 信号。"""
    assert _name_containment("高级工程师", "工程师")
    assert _name_containment("数据工程师", "数据")
    assert _name_containment("资深数据分析师", "数据分析师")


def test_name_containment_length_diff_below_two():
    """长度差 < 2 不建边（宁缺毋滥，防单字差异噪音）。"""
    assert not _name_containment("前端工程师", "前端工程")
    assert not _name_containment("分析师", "分析")


def test_name_containment_not_contained():
    """不包含/非连续包含不判定（宁缺毋滥）。"""
    assert not _name_containment("后端工程师", "前端工程师")
    assert not _name_containment("数据分析", "数据挖掘")
    # "前端工程师" 非 "前端开发工程师" 的连续子串（中间隔"开发"）→ 不判定
    assert not _name_containment("前端开发工程师", "前端工程师")


# ── _shared_segments ───────────────────────────────────────────────

def test_shared_segments_split_signal():
    """共享 ≥ 2 个连续 2 字片段 → split 信号。"""
    # "数据分析" 与 "大数据分析" 共享 "数据"+"分析" 2 个片段
    assert _shared_segments("大数据分析", "数据分析") >= 2
    # "机器学习" 与 "深度学习" 无共享 2 字片段（机/器/学/习 vs 深/度/学/习）
    assert _shared_segments("深度学习", "机器学习") < 2


def test_shared_segments_dedup():
    """片段按集合去重：重复片段只计一次。"""
    # "数据数据" 的片段集合与 "数据" 的交集只有 {"数据"} = 1
    assert _shared_segments("数据数据", "数据") == 1


def test_shared_segments_short_names():
    """短名（< seg_len）无片段可共享。"""
    assert _shared_segments("AI", "ML") == 0


# ── _position_nodes ────────────────────────────────────────────────

def test_position_nodes_filters_type_and_name():
    """只取 type=position 且有 name 的节点。"""
    snap = {
        "nodes": [
            _pos("p1", "前端工程师"),
            _pos("p2", ""),            # 空名剔除
            {"id": "s1", "name": "Python", "type": "skill"},  # 非岗位剔除
            {"id": "c1", "name": "课程", "type": "course"},
        ]
    }
    assert _position_nodes(snap) == {"p1": "前端工程师"}


def test_position_nodes_empty_snapshot():
    assert _position_nodes({}) == {}
    assert _position_nodes(None) == {}


def test_shared_segments_generic_suffix_excluded():
    """通用岗位词片段（工程/程师）不参与 split 判定——防任意工程师岗互建演化边。"""
    # 运维工程师 vs 算法工程师：共享 {工程, 程师} 均被排除 → 0
    assert _shared_segments("算法工程师", "运维工程师") == 0
    # 真实 split 信号仍保留：大数据分析 vs 数据分析 共享"数据"
    assert _shared_segments("大数据分析", "数据分析") >= 1


# ── derive_evolved_from ────────────────────────────────────────────

def test_derive_insufficient_versions(monkeypatch):
    """版本不足 2 个 → 直接返回空结果。"""
    def fake_session():
        class _S:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def scalars(self, stmt):
                return _Result([_version(1, [_pos("p1", "前端工程师")])])
        return _S()

    monkeypatch.setattr(
        "app.services.evolution.evolved_from.async_session_factory", fake_session)
    result = asyncio.run(derive_evolved_from())
    assert result["edges"] == 0
    assert "快照不足" in result["detail"]


def test_derive_no_change_no_edges(monkeypatch):
    """相邻快照岗位集合无变化 → 0 边。"""
    nodes = [_pos("p1", "前端工程师"), _pos("p2", "后端工程师")]
    def fake_session():
        class _S:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def scalars(self, stmt):
                return _Result([_version(1, nodes), _version(2, nodes)])
        return _S()

    monkeypatch.setattr(
        "app.services.evolution.evolved_from.async_session_factory", fake_session)
    result = asyncio.run(derive_evolved_from())
    assert result["edges"] == 0
    assert result["versions"] == ["v1", "v2"]


def test_derive_rename_edge_dry_run(monkeypatch):
    """rename 命中：dry_run 计数与真实执行一致。"""
    prev = [_pos("p1", "工程师")]
    cur = [_pos("p1", "高级工程师")]  # 工程师消失、高级工程师新增（连续包含差 2 → rename）
    def fake_session():
        class _S:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def scalars(self, stmt):
                return _Result([_version(1, prev), _version(2, cur)])
        return _S()

    monkeypatch.setattr(
        "app.services.evolution.evolved_from.async_session_factory", fake_session)
    result = asyncio.run(derive_evolved_from(dry_run=True))
    assert result["edges"] == 1
    assert result["new_positions"] == 1
    assert result["gone_positions"] == 1
    assert result["skipped"] == 0


def test_derive_split_edge_dry_run(monkeypatch):
    """split 命中（共享片段 ≥ 2）：dry_run 计数。"""
    prev = [_pos("p1", "数据分析师"), _pos("p2", "测试工程师")]
    cur = [_pos("p2", "测试工程师"), _pos("p3", "大数据分析工程师")]  # 分析师消失 → 新增岗
    def fake_session():
        class _S:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def scalars(self, stmt):
                return _Result([_version(1, prev), _version(2, cur)])
        return _S()

    monkeypatch.setattr(
        "app.services.evolution.evolved_from.async_session_factory", fake_session)
    result = asyncio.run(derive_evolved_from(dry_run=True))
    # "大数据分析工程师" vs "数据分析师"：共享 "数据"+"分析" ≥ 2 片段 → split
    assert result["edges"] == 1


def test_derive_unrelated_names_skipped(monkeypatch):
    """无包含/无共享片段 → 全部 skipped。"""
    prev = [_pos("p1", "数据工程师")]
    cur = [_pos("p1", "量化研究员")]  # 旧岗消失 + 新岗出现，无演化关系
    def fake_session():
        class _S:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def scalars(self, stmt):
                return _Result([_version(1, prev), _version(2, cur)])
        return _S()

    monkeypatch.setattr(
        "app.services.evolution.evolved_from.async_session_factory", fake_session)
    result = asyncio.run(derive_evolved_from(dry_run=True))
    assert result["edges"] == 0
    assert result["skipped"] >= 1


def test_derive_real_execution_merges_edge(monkeypatch):
    """真实执行：MERGE 建边（mock Neo4j 返回 covered=1）。"""
    prev = [_pos("p1", "工程师")]
    cur = [_pos("p1", "高级工程师")]

    class _FakeResult:
        def single(self):
            return {"covered": 1}

    class _FakeSession:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def run(self, query, **params):
            # 断言建边参数：change_type=rename、version=v2
            assert params["change_type"] == "rename"
            assert params["version"] == "v2"
            return _FakeResult()

    class _FakeDriver:
        def session(self):
            return _FakeSession()

    def fake_session():
        class _S:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def scalars(self, stmt):
                return _Result([_version(1, prev), _version(2, cur)])
        return _S()

    monkeypatch.setattr(
        "app.services.evolution.evolved_from.async_session_factory", fake_session)
    monkeypatch.setattr(
        "app.core.database.neo4j_driver", _FakeDriver())
    result = asyncio.run(derive_evolved_from())
    assert result["edges"] == 1


def test_derive_real_execution_single_session_via_thread(monkeypatch):
    """P2-12：真实执行写库经 to_thread，且多条边复用单 session（不再每边新开）。"""
    from app.services.evolution.evolved_from import _write_evolved_edges

    prev = [_pos("p1", "工程师"), _pos("p2", "数据分析")]
    cur = [_pos("p1", "高级工程师"), _pos("p2", "大数据分析")]
    # 高级工程师←rename←工程师；大数据分析←split←数据分析（共享"数据"+"分析"）

    run_calls: list[dict] = []

    class _FakeResult:
        def single(self):
            return {"covered": 1}

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, query, **params):
            run_calls.append(params)
            return _FakeResult()

    class _FakeDriver:
        def __init__(self):
            self.session_calls = 0

        def session(self):
            self.session_calls += 1
            return _FakeSession()

    driver = _FakeDriver()

    def fake_session():
        class _S:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def scalars(self, stmt):
                return _Result([_version(1, prev), _version(2, cur)])
        return _S()

    monkeypatch.setattr(
        "app.services.evolution.evolved_from.async_session_factory", fake_session)
    monkeypatch.setattr("app.core.database.neo4j_driver", driver)

    real_to_thread = asyncio.to_thread
    thread_fns: list = []

    async def _spy_to_thread(fn, *args, **kwargs):
        thread_fns.append(fn)
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(
        "app.services.evolution.evolved_from.asyncio",
        SimpleNamespace(to_thread=_spy_to_thread))

    result = asyncio.run(derive_evolved_from())

    assert result["edges"] == 2
    assert _write_evolved_edges in thread_fns  # 同步写库经 to_thread，不阻塞事件循环
    assert driver.session_calls == 1  # 2 条边复用同一 session
    assert len(run_calls) == 2
