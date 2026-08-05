"""LLM provider 健康检查定时任务测试（设计文档 §6.5）。

验证：WorkerSettings 注册了每 5min 的 cron job（含启动即跑）；任务在
配置缺失时跳过（返回原因）、正常时返回探测结果。不真实调用外部 API，
用 monkeypatch 替换 health_check_all。
"""

import pytest
from arq.cron import CronJob

from app.workers import tasks
from app.workers.tasks import WorkerSettings, check_llm_providers_health


def test_worker_settings_registers_health_check_cron():
    jobs = WorkerSettings.cron_jobs
    assert len(jobs) == 1
    job = jobs[0]
    assert isinstance(job, CronJob)
    assert job.coroutine is check_llm_providers_health
    # 每 5min 触发一次，worker 启动即跑一次（快速发现不可用 provider）
    assert job.minute == set(range(0, 60, 5))
    assert job.run_at_startup is True


def test_health_check_task_registered_in_functions():
    assert check_llm_providers_health in WorkerSettings.functions


@pytest.mark.asyncio
async def test_check_llm_providers_health_returns_ok(monkeypatch):
    from app.services.extraction import llm_provider as lp

    monkeypatch.setattr(lp, "health_check_all", lambda: {"primary": True, "backup": False})
    result = await check_llm_providers_health({})
    assert result["status"] == "ok"
    assert result["healthy"] == {"primary": True, "backup": False}


@pytest.mark.asyncio
async def test_check_llm_providers_health_skips_on_missing_config(monkeypatch):
    from app.services.extraction import llm_provider as lp
    from app.services.extraction.llm_provider import LLMConfigurationError

    def raise_config():
        raise LLMConfigurationError("LLM 配置缺失")

    monkeypatch.setattr(lp, "health_check_all", lambda: raise_config())
    result = await check_llm_providers_health({})
    assert result["status"] == "skipped"
    assert "配置缺失" in result["reason"]
