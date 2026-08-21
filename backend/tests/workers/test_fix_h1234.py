"""08-21b 高优先级修复测试（H1/H3/H4）。

- H1: run_etl_pipeline 主管线跳过已配独立 hour/minute 的爬虫（防双跑）
- H3: icourse163 数字安全解析（_safe_int/_safe_float）
- H4: zhilian __INITIAL_STATE__ 括号配平提取（原贪婪正则吞 JS 导致 publishTime 丢失）
"""

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))


# ── H1: 主管线跳过独立调度爬虫 ──

def test_etl_pipeline_skips_independent_schedule(monkeypatch):
    """已配置 hour/minute 的爬虫由 crawl_scheduler 独立触发，主管线跳过（防双跑）。"""
    import asyncio

    from app.workers import etl

    calls = []

    async def _fake_crawl(ctx, spider, **kwargs):
        calls.append(spider)
        return {"spider": spider}

    async def _stub_stage(*a, **k):
        return {"stub": True}

    def _stub_task(*a, **k):
        # 同步返回 dict：避免 async stub 产生的 coroutine 未被 await 的警告
        return {}

    # 后续阶段任务全部桩掉（run_etl_pipeline 会引用 tasks_module.<stage>）
    class _FakeTasks:
        CDP_SPIDERS = set()
        crawl_platform = staticmethod(_fake_crawl)
        _run_stage = staticmethod(_stub_stage)
        _run_limited_stage = staticmethod(_stub_stage)
        dedup_simhash = staticmethod(_stub_task)
        batch_extract = staticmethod(_stub_task)
        validate_temporal = staticmethod(_stub_task)
        detect_inflation = staticmethod(_stub_task)
        enrich_course_skills = staticmethod(_stub_task)
        load_courses = staticmethod(_stub_task)
        evaluate_courses = staticmethod(_stub_task)
        aggregate_positions = staticmethod(_stub_task)
        cross_validate_jds = staticmethod(_stub_task)
        sync_skill_normalization = staticmethod(_stub_task)
        diversity_report = staticmethod(_stub_task)
        check_data_freshness = staticmethod(_stub_task)
        backfill_embeddings = staticmethod(_stub_task)
        snapshot_graph = staticmethod(_stub_task)
        discovery_daily = staticmethod(_stub_task)
        discovery_auto_transition = staticmethod(_stub_task)
        graph_health_check = staticmethod(_stub_task)
        dict_guard_daily = staticmethod(_stub_task)

    # zhilian 配独立 hour/minute → 主管线跳过；arxiv 未配 → 主管线执行
    # 注意：runtime_config.get("crawlers") 直接返回爬虫配置 dict（无外层 crawlers 键）
    monkeypatch.setattr(
        etl.runtime_config,
        "get",
        lambda key, default=None: (
            {
                "zhilian": {"enabled": True, "hour": 4, "minute": 1},
                "arxiv": {"enabled": True},
            }
            if key == "crawlers"
            else default
        ),
    )
    # monkeypatch 生效自检：crawlers 读取应返回带独立时间的 zhilian 配置
    assert etl.runtime_config.get("crawlers")["zhilian"].get("hour") == 4

    result = asyncio.run(
        etl.run_etl_pipeline({}, run_date="2026-08-21", tasks_module=_FakeTasks)
    )

    crawl = result["stages"]["crawl"]
    by_spider = {r["spider"]: r for r in crawl}
    assert by_spider["zhilian"]["skipped"] == "independent_schedule"
    assert "arxiv" in calls  # 未配独立时间的爬虫仍在主管线执行
    assert "zhilian" not in calls  # 配了独立时间的爬虫不双跑


# ── H3: icourse163 数字安全解析 ──

def test_safe_int_normal():
    from crawlers.icourse163_crawler import _safe_int
    assert _safe_int(3600) == 3600
    assert _safe_int("3600") == 3600
    assert _safe_int("3,600") == 3600
    assert _safe_int("1.2万") == 12000
    assert _safe_int("8000人") == 0  # 带非数字后缀无法解析 → 0 而非崩溃


def test_safe_int_bad():
    from crawlers.icourse163_crawler import _safe_int
    assert _safe_int("N/A") == 0
    assert _safe_int(None) == 0
    assert _safe_int(True) == 0
    assert _safe_int("") == 0


def test_safe_float_normal():
    from crawlers.icourse163_crawler import _safe_float
    assert _safe_float(4.5) == 4.5
    assert _safe_float("4.5") == 4.5
    assert _safe_float("4.5分") == 0.0


def test_safe_float_bad():
    from crawlers.icourse163_crawler import _safe_float
    assert _safe_float(None) == 0.0
    assert _safe_float("abc") == 0.0


# ── H4: zhilian __INITIAL_STATE__ 括号配平 ──

def test_extract_initial_state_basic():
    from crawlers.spiders.zhilian import _extract_initial_state
    html = "__INITIAL_STATE__={\"a\": {\"b\": [1, 2]}, \"publishTime\": \"2026-08-01T10:00:00\"};"
    data = _extract_initial_state(html)
    assert data is not None
    assert data["publishTime"] == "2026-08-01T10:00:00"
    assert data["a"]["b"] == [1, 2]


def test_extract_initial_state_with_trailing_js():
    """script 内 JSON 后仍有其它 JS：贪婪正则会吞掉导致解析失败，括号配平应正确。"""
    from crawlers.spiders.zhilian import _extract_initial_state
    html = (
        "__INITIAL_STATE__={\"number\": \"CC123\", \"publishTime\": \"2026-08-01T10:00:00\"};"
        "var x = {foo: 1}; var y = function(){return {a: 2}};"
    )
    data = _extract_initial_state(html)
    assert data is not None
    assert data["number"] == "CC123"
    assert data["publishTime"] == "2026-08-01T10:00:00"


def test_extract_initial_state_with_string_braces():
    """字符串内含花括号不得干扰配平。"""
    from crawlers.spiders.zhilian import _extract_initial_state
    html = '__INITIAL_STATE__={"desc": "包含 {花括号} 的字符串", "n": 1};'
    data = _extract_initial_state(html)
    assert data is not None
    assert data["desc"] == "包含 {花括号} 的字符串"


def test_extract_initial_state_missing():
    from crawlers.spiders.zhilian import _extract_initial_state
    assert _extract_initial_state("var a = 1;") is None
    assert _extract_initial_state("") is None
    assert _extract_initial_state("__INITIAL_STATE__=not json") is None