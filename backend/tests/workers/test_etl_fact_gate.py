"""ETL 事实门禁测试（08-23 全流程闭环审查 P0）。

- 事实阶段（去重/抽取入图/岗位聚合）任一硬失败 → pipeline_status=degraded，
  快照/演化/画像缓存/新岗位发现整体跳过并记 skipped 审计项；治理/报告阶段
  （健康检查/字典守卫/统计）不依赖当日数据完整性，继续执行。
- complete 路径阶段顺序：先发布当日快照，演化关系推导在其后（基于
  「上一版本+当日本版本」配对，旧顺序滞后一轮）。
"""

import asyncio
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))


def _build_env(monkeypatch, *, aggregate_fails: bool):
    """构造 run_etl_pipeline 的最小桩环境，返回 (calls, _FakeTasks)。"""
    from app.workers import etl

    calls: list[str] = []

    async def _fake_crawl(ctx, spider, **kwargs):
        return {"spider": spider}

    async def _stub_limited_stage(_name, *, task, ctx, task_kwargs=None, **_kw):
        return await task(ctx, **(task_kwargs or {}))

    async def _ok_task(ctx, **_kwargs):
        return {}

    async def _fail_aggregate(ctx):
        raise RuntimeError("聚合失败（模拟事实阶段硬失败）")

    class _FakeNeo4jSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class _FakeNeo4jDriver:
        def session(self):
            return _FakeNeo4jSession()

    async def _stub_derive():
        calls.append("evolved_from")
        return {}

    async def _stub_load_positions_shared():
        calls.append("positions_cache_prebuild")
        return []

    def _make_task(name):
        async def _task(ctx, **_kwargs):
            calls.append(name)
            return {}
        return _task

    class _FakeTasks:
        CDP_SPIDERS = set()
        crawl_platform = staticmethod(_fake_crawl)
        _run_stage = staticmethod(etl._run_stage)  # 真实阶段隔离：异常→error 项
        _run_limited_stage = staticmethod(_stub_limited_stage)
        dedup_simhash = staticmethod(_make_task("dedup_simhash"))
        batch_extract = staticmethod(_make_task("structure_load"))
        validate_temporal = staticmethod(_ok_task)
        detect_inflation = staticmethod(_ok_task)
        enrich_course_skills = staticmethod(_ok_task)
        load_courses = staticmethod(_ok_task)
        evaluate_courses = staticmethod(_ok_task)
        aggregate_positions = staticmethod(
            _fail_aggregate if aggregate_fails else _make_task("aggregate_positions")
        )
        cross_validate_jds = staticmethod(_ok_task)
        sync_skill_normalization = staticmethod(_ok_task)
        diversity_report = staticmethod(_ok_task)
        check_data_freshness = staticmethod(_ok_task)
        backfill_embeddings = staticmethod(_make_task("backfill_embeddings"))
        snapshot_graph = staticmethod(_make_task("snapshot_graph"))
        discovery_daily = staticmethod(_make_task("discovery_daily"))
        discovery_auto_transition = staticmethod(_make_task("discovery_auto_transition"))
        graph_health_check = staticmethod(_make_task("graph_health_check"))
        dict_guard_daily = staticmethod(_make_task("dict_guard"))
        llm_stats_daily = staticmethod(_make_task("llm_stats"))
        skill_category_review_daily = staticmethod(_make_task("skill_category_review"))
        name_normalization_shadow_daily = staticmethod(_make_task("name_normalization_shadow"))
        name_normalization_propose_daily = staticmethod(_make_task("name_normalization_propose"))
        skill_relation_propose_daily = staticmethod(_make_task("skill_relation_propose"))

    # 编排器内直接导入的阶段模块以桩替换（防真实图数据库/演化/缓存副作用）
    database_stub = ModuleType("app.core.database")
    database_stub.neo4j_driver = _FakeNeo4jDriver()
    skill_relations_stub = ModuleType("app.services.kg.skill_relations")
    skill_relations_stub.sync_skill_relations = lambda session: {}
    evolved_from_stub = ModuleType("app.services.evolution.evolved_from")
    evolved_from_stub.derive_evolved_from = _stub_derive
    shared_cache_stub = ModuleType("app.services.matching.shared_cache")
    shared_cache_stub.load_positions_shared = _stub_load_positions_shared
    for name, stub in (
        ("app.core.database", database_stub),
        ("app.services.kg.skill_relations", skill_relations_stub),
        ("app.services.evolution.evolved_from", evolved_from_stub),
        ("app.services.matching.shared_cache", shared_cache_stub),
    ):
        monkeypatch.setitem(sys.modules, name, stub)

    import app.services.alerting as alerting_mod

    monkeypatch.setattr(alerting_mod, "send_alert", AsyncMock())
    return calls, _FakeTasks


def test_fact_failure_degrades_pipeline(monkeypatch):
    """聚合（事实阶段）硬失败 → 快照/演化/发现跳过，治理阶段继续。"""
    from app.workers import etl

    calls, fake_tasks = _build_env(monkeypatch, aggregate_fails=True)

    result = asyncio.run(
        etl.run_etl_pipeline({}, run_date="2026-08-23", tasks_module=fake_tasks)
    )

    assert result["pipeline_status"] == "degraded"
    for stage in (
        "backfill_embeddings",
        "snapshot_graph",
        "evolved_from",
        "positions_cache_prebuild",
        "discovery_daily",
        "discovery_auto_transition",
    ):
        assert result["stages"][stage] == {
            "skipped": "pipeline_degraded",
            "failed_fact_stages": ["aggregate_positions"],
        }, stage
        assert stage not in calls  # 门禁阶段未执行
    # 治理/报告阶段不依赖当日数据完整性，继续执行
    for stage in ("graph_health_check", "dict_guard", "llm_stats", "skill_category_review"):
        assert stage in calls
    assert result["stages"]["aggregate_positions"]["error"].startswith("RuntimeError")


def test_complete_path_snapshots_before_evolved_from(monkeypatch):
    """complete 路径：快照发布在演化推导之前（当日新数据当轮参与演化）。"""
    from app.workers import etl

    calls, fake_tasks = _build_env(monkeypatch, aggregate_fails=False)

    result = asyncio.run(
        etl.run_etl_pipeline({}, run_date="2026-08-23", tasks_module=fake_tasks)
    )

    assert result["pipeline_status"] == "complete"
    assert "snapshot_graph" in result["stages"] and "error" not in result["stages"]["snapshot_graph"]
    assert calls.index("snapshot_graph") < calls.index("evolved_from")
    assert calls.index("aggregate_positions") < calls.index("snapshot_graph")
    assert "discovery_daily" in calls
