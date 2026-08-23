"""llm_stats_daily worker 任务测试（Redis 聚合 → 报告落盘 → ETL 注册契约）。"""

import asyncio
import json


from app.workers import settings as worker_settings
from app.workers.llm_stats import llm_stats_daily


class _FakeRedis:
    def __init__(self, kv: dict[str, int]):
        # 键统一 str（worker 对 bytes/str 均兼容，此处取简）
        self._kv = dict(kv)

    def scan_iter(self, match=None):
        prefix = match.rstrip("*") if match else ""
        for key in list(self._kv):
            if key.startswith(prefix):
                yield key

    def get(self, key):
        value = self._kv.get(key)
        return str(value).encode() if value is not None else None


def _run(kv: dict, monkeypatch, jsonl_lines: list[str] | None = None):
    from app.services.extraction import llm_invocation
    import app.workers.llm_stats as llm_stats_module

    monkeypatch.setattr(llm_invocation, "_redis_client", _FakeRedis(kv))
    tmp_reports = kv.pop("_tmp", None)
    if tmp_reports is not None:
        monkeypatch.setattr(llm_stats_module, "_REPORT_DIR", tmp_reports)
    if jsonl_lines is not None:
        inv_dir = tmp_reports / "llm_invocations"
        inv_dir.mkdir(parents=True, exist_ok=True)
        (inv_dir / f"{_yesterday()}.jsonl").write_text(
            "\n".join(jsonl_lines), encoding="utf-8"
        )
    return asyncio.run(llm_stats_daily({}))


def _yesterday():
    from datetime import datetime, timedelta, timezone

    cst = timezone(timedelta(hours=8))
    return (datetime.now(cst) - timedelta(days=1)).strftime("%Y-%m-%d")


class TestLlmStatsDaily:
    def test_aggregates_and_writes_report(self, tmp_path, monkeypatch):
        ymd = _yesterday()
        kv = {
            f"llm:stats:{ymd}:primary:ok": 3,
            f"llm:stats:{ymd}:primary:calls_total": 4,
            f"llm:stats:{ymd}:primary:timeout": 1,
            f"llm:stats:{ymd}:primary:latency_ms_sum": 800,
            "llm:circuit:primary": 1,  # 非 stats 键不进报告
            "_tmp": tmp_path,
        }
        summary = _run(kv, monkeypatch)

        assert summary["status"] == "ok"
        assert summary["run_date"] == ymd
        assert set(summary["providers"]) == {"primary"}
        assert summary["providers"]["primary"]["ok_rate"] == 0.75
        report = json.loads(
            (tmp_path / f"llm_stats_{ymd}.json").read_text(encoding="utf-8")
        )
        assert report == summary

    def test_jsonl_enriches_purposes(self, tmp_path, monkeypatch):
        ymd = _yesterday()
        kv = {
            f"llm:stats:{ymd}:primary:ok": 2,
            f"llm:stats:{ymd}:primary:calls_total": 2,
            "_tmp": tmp_path,
        }
        summary = _run(
            kv, monkeypatch,
            jsonl_lines=[
                json.dumps({"purpose": "jd_extract"}),
                json.dumps({"purpose": "jd_extract"}),
                json.dumps({"purpose": "position_review"}),
            ],
        )
        assert summary["purposes"] == {
            "jd_extract": 2, "position_review": 1,
        }

    def test_no_data_returns_empty_ok(self, tmp_path, monkeypatch):
        summary = _run({"_tmp": tmp_path}, monkeypatch)
        assert summary["status"] == "ok"
        assert summary["providers"] == {}
        assert "purposes" not in summary


class TestRegistrationContract:
    """ARQ 门面/WorkerSettings/ETL 阶段链三处注册一致性（防任务静默失联）。"""

    def test_task_registered_in_worker_settings(self):

        assert llm_stats_daily in worker_settings.WorkerSettings.functions

    def test_facade_reexports_same_function(self):
        from app.workers import tasks as facade
        from app.workers.llm_stats import llm_stats_daily as impl

        assert facade.llm_stats_daily is impl

    def test_etl_stage_chain_references_task(self):
        import inspect

        import app.workers.etl as etl_module

        source = inspect.getsource(etl_module)
        assert "tasks_module.llm_stats_daily(ctx)" in source
        assert '"llm_stats"' in source
