"""LLMProviderChain 单元测试（设计文档 §6.5 多 Provider 重试链）。

覆盖：yaml 加载（priority 排序 / enabled 过滤）、未配置抛错、
call_sync 单次尝试不切换、call_with_fallback 按优先级切换、全失败聚合。
外部 API 调用（instructor/openai）不在此层测试，仅测链语义。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from app.services.extraction import llm_provider as llm_provider_module
from app.services.extraction.llm_provider import (
    LLMConfigurationError,
    LLMExtractionError,
    LLMProviderChain,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    check_provider_health,
    health_check_all,
)


class _DemoModel(BaseModel):
    """测试用的响应模型。"""

    value: str


def _write_config(path: Path, providers: list[dict]) -> None:
    path.write_text(
        yaml.safe_dump({"providers": providers}, allow_unicode=True),
        encoding="utf-8",
    )


def _make_chain(tmp_path: Path) -> tuple[LLMProviderChain, Path]:
    path = tmp_path / "llm.yaml"
    _write_config(path, [
        {"name": "primary", "priority": 1, "api_key": "k1", "enabled": True},
        {"name": "backup", "priority": 2, "api_key": "k2", "enabled": True},
    ])
    return LLMProviderChain(config_path=path), path


class TestLoadProviders:
    def test_sorted_by_priority_and_filtered_by_enabled(self, tmp_path):
        path = tmp_path / "llm.yaml"
        _write_config(path, [
            {"name": "c", "priority": 3, "api_key": "k3", "enabled": True},
            {"name": "disabled", "priority": 1, "api_key": "k0", "enabled": False},
            {"name": "a", "priority": 1, "api_key": "k1", "enabled": True},
            {"name": "b", "priority": 2, "api_key": "k2", "enabled": True},
        ])
        chain = LLMProviderChain(config_path=path)
        names = [p["name"] for p in chain._providers]
        assert names == ["a", "b", "c"]

    def test_all_disabled_yields_empty(self, tmp_path):
        path = tmp_path / "llm.yaml"
        _write_config(path, [
            {"name": "x", "priority": 1, "api_key": "k", "enabled": False},
        ])
        chain = LLMProviderChain(config_path=path)
        assert chain._providers == []

    def test_invalid_protocol_rejected_at_construction(self, tmp_path):
        """enabled provider 协议非法（如拼写 antropic）→ 构造期即抛错。

        审查①：此前非法值静默回退 openai，请求会发往错误端点且排查困难。
        """
        path = tmp_path / "llm.yaml"
        _write_config(path, [
            {"name": "typo", "priority": 1, "api_key": "k1",
             "enabled": True, "protocol": "antropic"},
        ])
        with pytest.raises(LLMConfigurationError, match="protocol"):
            LLMProviderChain(config_path=path)


class TestUnconfigured:
    def test_missing_config_raises(self, tmp_path):
        # yaml 缺失在构造时即抛错（fail-fast）
        with pytest.raises(LLMConfigurationError):
            LLMProviderChain(config_path=tmp_path / "nope.yaml")

    def test_no_enabled_provider_raises(self, tmp_path):
        path = tmp_path / "llm.yaml"
        _write_config(path, [
            {"name": "x", "priority": 1, "api_key": "k", "enabled": False},
        ])
        chain = LLMProviderChain(config_path=path)
        with pytest.raises(LLMConfigurationError):
            chain.call_sync("prompt", _DemoModel)
        with pytest.raises(LLMConfigurationError):
            chain.call_with_fallback("prompt", _DemoModel)


class TestCallSync:
    def test_single_try_no_fallback(self, tmp_path, monkeypatch):
        chain, _ = _make_chain(tmp_path)
        called: list[str] = []

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            called.append(provider["name"])
            raise LLMTimeoutError("超时")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        with pytest.raises(LLMTimeoutError):
            chain.call_sync("prompt", _DemoModel)
        # 同步路由只尝试主 provider，不切换备 provider
        assert called == ["primary"]


class TestCallWithFallback:
    def test_primary_success_stops_chain(self, tmp_path, monkeypatch):
        chain, _ = _make_chain(tmp_path)
        called: list[str] = []

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            called.append(provider["name"])
            return _DemoModel(value="ok")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        result = chain.call_with_fallback("prompt", _DemoModel)
        assert result.value == "ok"
        assert called == ["primary"]

    def test_primary_failure_switches_to_backup(self, tmp_path, monkeypatch):
        chain, _ = _make_chain(tmp_path)
        called: list[str] = []

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            called.append(provider["name"])
            if provider["name"] == "primary":
                raise LLMTimeoutError("主 provider 超时")
            return _DemoModel(value="from-backup")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        result = chain.call_with_fallback("prompt", _DemoModel)
        assert called == ["primary", "backup"]
        assert result.value == "from-backup"

    def test_all_failed_aggregates_errors(self, tmp_path, monkeypatch):
        chain, _ = _make_chain(tmp_path)

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            raise LLMExtractionError(f"{provider['name']} 挂了")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        with pytest.raises(LLMExtractionError) as exc_info:
            chain.call_with_fallback("prompt", _DemoModel)
        msg = str(exc_info.value)
        assert "所有 provider 均失败" in msg
        assert "primary 挂了" in msg
        assert "backup 挂了" in msg

    def test_all_timeout_raises_timeout_error(self, tmp_path, monkeypatch):
        """全部 provider 均超时 → LLMTimeoutError（上层映射 504/5003，§2.4.7）。"""
        chain, _ = _make_chain(tmp_path)

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            raise LLMTimeoutError(f"{provider['name']} 超时")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        with pytest.raises(LLMTimeoutError) as exc_info:
            chain.call_with_fallback("prompt", _DemoModel)
        assert "所有 provider 均超时/不可用" in str(exc_info.value)

    def test_mixed_failure_keeps_parent_error(self, tmp_path, monkeypatch):
        """一个超时 + 一个连接失败（混合）→ 维持父类 LLMExtractionError（503 语义）。"""
        chain, _ = _make_chain(tmp_path)

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            if provider["name"] == "primary":
                raise LLMTimeoutError("主 provider 超时")
            raise LLMExtractionError("备 provider 连接失败")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        with pytest.raises(LLMExtractionError) as exc_info:
            chain.call_with_fallback("prompt", _DemoModel)
        assert type(exc_info.value) is LLMExtractionError

    def test_wall_budget_skips_remaining_providers(self, tmp_path, monkeypatch):
        """墙钟预算耗尽（第八轮裁决②）：剩余 provider 不再尝试，记超时语义。"""
        chain, _ = _make_chain(tmp_path)
        called: list[str] = []

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            called.append(provider["name"])
            raise LLMTimeoutError(f"{provider['name']} 超时")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        # 伪时钟：每次读数 +100s → 首 provider 尝试后墙钟即超预算
        counter = {"t": 0.0}

        def fake_perf_counter():
            counter["t"] += 100.0
            return counter["t"]

        monkeypatch.setattr(llm_provider_module.time, "perf_counter", fake_perf_counter)
        with pytest.raises(LLMTimeoutError) as exc_info:
            chain.call_with_fallback("prompt", _DemoModel, wall_budget=90)
        # 首个 provider 恒尝试（index>1 才检查预算），backup 不再发起调用
        assert called == ["primary"]
        msg = str(exc_info.value)
        assert "墙钟预算 90s 已耗尽" in msg
        assert "剩余 1 个 provider 不再尝试" in msg

    def test_wall_budget_reads_runtime_knob_when_unset(self, tmp_path, monkeypatch):
        """wall_budget 缺省读 runtime 旋钮 llm_async_wall_budget。"""
        chain, _ = _make_chain(tmp_path)
        called: list[str] = []

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            called.append(provider["name"])
            raise LLMTimeoutError(f"{provider['name']} 超时")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        monkeypatch.setattr(
            llm_provider_module.runtime_config, "get",
            lambda key, default=None: 60 if key == "llm_async_wall_budget" else default,
        )
        counter = {"t": 0.0}

        def fake_perf_counter():
            counter["t"] += 100.0
            return counter["t"]

        monkeypatch.setattr(llm_provider_module.time, "perf_counter", fake_perf_counter)
        with pytest.raises(LLMTimeoutError) as exc_info:
            chain.call_with_fallback("prompt", _DemoModel)
        assert called == ["primary"]
        assert "墙钟预算 60s 已耗尽" in str(exc_info.value)


class TestStructuredOutputParams:
    """设计文档 §6.2 参数：max_tokens=2048 / top_p=0.9，yaml 可覆盖。"""

    def test_defaults_when_structured_output_missing(self, tmp_path):
        path = tmp_path / "llm.yaml"
        _write_config(path, [{"name": "x", "priority": 1, "api_key": "k", "enabled": True}])
        chain = LLMProviderChain(config_path=path)
        assert chain._max_tokens == 2048
        assert chain._top_p == 0.9

    def test_reads_from_yaml_structured_output(self, tmp_path):
        path = tmp_path / "llm.yaml"
        data = {
            "providers": [{"name": "x", "priority": 1, "api_key": "k", "enabled": True}],
            "structured_output": {"temperature": 0.3, "max_tokens": 4096, "top_p": 0.8},
        }
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        chain = LLMProviderChain(config_path=path)
        assert chain._temperature == 0.3
        assert chain._max_tokens == 4096
        assert chain._top_p == 0.8

    def test_invalid_values_fall_back_to_defaults(self, tmp_path):
        path = tmp_path / "llm.yaml"
        data = {
            "providers": [{"name": "x", "priority": 1, "api_key": "k", "enabled": True}],
            "structured_output": {"max_tokens": "abc", "top_p": "xyz"},
        }
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        chain = LLMProviderChain(config_path=path)
        assert chain._max_tokens == 2048
        assert chain._top_p == 0.9


# ============================================================
# §6.5 运维机制：429 退避 / 5xx 熔断 / 健康检查 / fail-open
# ============================================================


class _FakeRedis:
    """内存 dict 后端，替换真实 Redis client（_redis_* 真实实现走它）。

    隔离外部 Redis 依赖：autouse 注入后，所有测试共享同一个假后端，
    fail-open 边界（_redis_* 的 try/except）保持真实实现。
    """

    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value
        return True

    def delete(self, key):
        self.data.pop(key, None)
        return 1

    def incr(self, key):
        self.data[key] = str(int(self.data.get(key, 0)) + 1)
        return int(self.data[key])

    def expire(self, key, ttl):
        return True


@pytest.fixture(autouse=True)
def _fake_store(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(llm_provider_module, "_get_redis_client", lambda: fake)
    return fake


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1_000_000.0}
    monkeypatch.setattr(llm_provider_module, "_now", lambda: now["t"])
    return now


class TestRateLimitBackoff:
    """429 指数退避：写截止时间 → 窗口内跳过 → 30→60→120s 递进封顶。"""

    def _fake_chain(self, chain, monkeypatch, primary_exc):
        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            if provider["name"] == "primary":
                raise primary_exc
            return _DemoModel(value="ok")

        monkeypatch.setattr(chain, "_call_provider", fake_call)

    def test_429_records_backoff_and_skips_next_call(self, tmp_path, monkeypatch, _fake_store, clock):
        chain, _ = _make_chain(tmp_path)
        self._fake_chain(chain, monkeypatch, LLMRateLimitError("429 限流"))
        calls: list[str] = []

        def spy(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            calls.append(provider["name"])
            if provider["name"] == "primary":
                raise LLMRateLimitError("429 限流")
            return _DemoModel(value="ok")

        monkeypatch.setattr(chain, "_call_provider", spy)
        result = chain.call_with_fallback("prompt", _DemoModel)
        assert result.value == "ok"
        assert calls == ["primary", "backup"]
        # 首次 429：退避 30s（截止时间 = now + 30），计数 = 1，TTL 与退避期一致
        assert _fake_store.data["llm:backoff:primary"] == str(clock["t"] + 30)
        assert _fake_store.data["llm:backoff_count:primary"] == "1"
        # 退避窗口内再次调用：primary 被跳过，直接走 backup
        calls.clear()
        chain.call_with_fallback("prompt", _DemoModel)
        assert calls == ["backup"]

    def test_429_backoff_escalates_30_60_120(self, tmp_path, monkeypatch, _fake_store, clock):
        chain, _ = _make_chain(tmp_path)
        self._fake_chain(chain, monkeypatch, LLMRateLimitError("429 限流"))
        # 连续第 1/2/3/4 次 429 → 退避 30/60/120/120（时长封顶，计数继续累加）
        count = 0
        for expected in (30, 60, 120, 120):
            count += 1
            chain.call_with_fallback("prompt", _DemoModel)
            assert _fake_store.data["llm:backoff:primary"] == str(clock["t"] + expected)
            assert _fake_store.data["llm:backoff_count:primary"] == str(count)
            # 拨快时钟越过退避窗口，使下一次调用再次命中 primary 的 429
            clock["t"] += expected + 1


class TestCircuitBreaker:
    """连续 3 次 5xx 熔断 5min：窗口内跳过、过期探活、成功恢复。"""

    def _failing_primary(self, chain, monkeypatch):
        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            if provider["name"] == "primary":
                raise LLMServerError("500 服务器错误")
            return _DemoModel(value="ok")

        monkeypatch.setattr(chain, "_call_provider", fake_call)

    def test_three_consecutive_5xx_opens_circuit(self, tmp_path, monkeypatch, _fake_store, clock):
        chain, _ = _make_chain(tmp_path)
        self._failing_primary(chain, monkeypatch)
        # 前两次 5xx：backup 兜底，仅累计计数
        for expected in ("1", "2"):
            chain.call_with_fallback("prompt", _DemoModel)
            assert _fake_store.data["llm:5xx_count:primary"] == expected
        assert "llm:circuit:primary" not in _fake_store.data
        # 第三次 5xx → 熔断 5min，计数清零
        chain.call_with_fallback("prompt", _DemoModel)
        assert _fake_store.data["llm:circuit:primary"] == str(clock["t"] + 300)
        assert "llm:5xx_count:primary" not in _fake_store.data

    def test_circuit_open_skips_provider(self, tmp_path, monkeypatch, _fake_store, clock):
        chain, _ = _make_chain(tmp_path)
        _fake_store.data["llm:circuit:primary"] = str(clock["t"] + 300)
        calls: list[str] = []

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            calls.append(provider["name"])
            return _DemoModel(value="ok")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        chain.call_with_fallback("prompt", _DemoModel)
        assert calls == ["backup"]  # 熔断窗口内 primary 不发起调用

    def test_circuit_expired_allows_trial_and_failure_reopens(self, tmp_path, monkeypatch, _fake_store, clock):
        chain, _ = _make_chain(tmp_path)
        # 熔断 key 残留但已过期（窗口结束 → 自动进入探活）
        _fake_store.data["llm:circuit:primary"] = str(clock["t"] - 1)
        _fake_store.data["llm:5xx_count:primary"] = "2"
        calls: list[str] = []

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            calls.append(provider["name"])
            if provider["name"] == "primary":
                raise LLMServerError("仍然 500")
            return _DemoModel(value="ok")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        chain.call_with_fallback("prompt", _DemoModel)
        assert calls == ["primary", "backup"]  # 过期后允许探活
        # 探活失败：从既有计数继续累计 → 再次熔断 5min
        assert _fake_store.data["llm:circuit:primary"] == str(clock["t"] + 300)

    def test_success_after_window_clears_state(self, tmp_path, monkeypatch, _fake_store, clock):
        chain, _ = _make_chain(tmp_path)
        # 熔断/退避 key 均已过期，探活成功 → 清除全部状态（成功即恢复）
        _fake_store.data["llm:circuit:primary"] = str(clock["t"] - 1)
        _fake_store.data["llm:5xx_count:primary"] = "2"
        _fake_store.data["llm:backoff:primary"] = str(clock["t"] - 1)

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            return _DemoModel(value="ok")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        result = chain.call_with_fallback("prompt", _DemoModel)
        assert result.value == "ok"
        for key in ("llm:circuit:primary", "llm:5xx_count:primary",
                    "llm:backoff:primary", "llm:backoff_count:primary"):
            assert key not in _fake_store.data


class TestCallSyncDowngrade:
    """熔断/退避对同步路由同样生效：命中窗口或 429/5xx 均抛 LLMTimeoutError（上层映射 504）。"""

    def test_sync_skips_backed_off_provider(self, tmp_path, _fake_store, clock):
        chain, _ = _make_chain(tmp_path)
        _fake_store.data["llm:backoff:primary"] = str(clock["t"] + 60)
        with pytest.raises(LLMTimeoutError):
            chain.call_sync("prompt", _DemoModel)

    def test_sync_429_records_backoff_and_raises_timeout(self, tmp_path, monkeypatch, _fake_store, clock):
        chain, _ = _make_chain(tmp_path)

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            raise LLMRateLimitError("429 限流")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        with pytest.raises(LLMTimeoutError):
            chain.call_sync("prompt", _DemoModel)
        # 同步路径也记录退避状态，供后续异步路径生效
        assert _fake_store.data["llm:backoff:primary"] == str(clock["t"] + 30)

    def test_sync_5xx_records_and_raises_timeout(self, tmp_path, monkeypatch, _fake_store):
        chain, _ = _make_chain(tmp_path)

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            raise LLMServerError("500 服务器错误")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        with pytest.raises(LLMTimeoutError):
            chain.call_sync("prompt", _DemoModel)
        assert _fake_store.data["llm:5xx_count:primary"] == "1"


class TestHealthCheck:
    """/models 探测：200 → healthy，非 200/异常 → unhealthy，结果写 Redis。"""

    class _FakeResponse:
        """支持 with 协议的假 urlopen 响应（模拟 urllib 的 HTTPResponse）。"""

        def __init__(self, status):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _provider(self):
        return {"name": "primary", "base_url": "https://api.test.com/v1/", "api_key": "k"}

    def test_200_marks_healthy(self, monkeypatch, _fake_store):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout: self._FakeResponse(200),
        )
        assert check_provider_health(self._provider()) is True
        assert _fake_store.data["llm:health:primary"] == "1"

    def test_non_200_marks_unhealthy(self, monkeypatch, _fake_store):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout: self._FakeResponse(503),
        )
        assert check_provider_health(self._provider()) is False
        assert _fake_store.data["llm:health:primary"] == "0"

    def test_network_error_marks_unhealthy(self, monkeypatch, _fake_store):
        def boom(req, timeout):
            raise OSError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        assert check_provider_health(self._provider()) is False
        assert _fake_store.data["llm:health:primary"] == "0"

    def test_missing_base_url_marks_unhealthy(self, _fake_store):
        provider = {"name": "primary", "api_key": "k"}
        assert check_provider_health(provider) is False
        assert _fake_store.data["llm:health:primary"] == "0"

    def test_health_check_all_iterates_enabled(self, monkeypatch):
        class _FakeChain:
            _providers = [
                {"name": "a", "base_url": "http://a", "api_key": "k"},
                {"name": "b", "base_url": "http://b", "api_key": "k"},
            ]

        monkeypatch.setattr(llm_provider_module, "LLMProviderChain", _FakeChain)
        monkeypatch.setattr(
            llm_provider_module, "check_provider_health", lambda p, timeout=None: True
        )
        assert health_check_all() == {"a": True, "b": True}


class TestRedisFailOpen:
    """Redis 不可用时降级机制静默跳过，不阻塞主调用链。"""

    class _BoomClient:
        def get(self, key):
            raise RuntimeError("connection refused")

        def set(self, *a, **k):
            raise RuntimeError("connection refused")

        def delete(self, *a, **k):
            raise RuntimeError("connection refused")

    @pytest.fixture
    def boom_redis(self, monkeypatch):
        # 覆盖 autouse 假后端：_redis_* 真实实现走抛异常 client，fail-open 生效
        monkeypatch.setattr(
            llm_provider_module, "_get_redis_client", lambda: self._BoomClient()
        )
        return None

    def test_call_chain_works_without_redis(self, tmp_path, monkeypatch, boom_redis):
        chain, _ = _make_chain(tmp_path)

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            return _DemoModel(value="ok")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        assert chain.call_with_fallback("prompt", _DemoModel).value == "ok"

    def test_429_recording_does_not_block_chain(self, tmp_path, monkeypatch, boom_redis):
        chain, _ = _make_chain(tmp_path)

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            if provider["name"] == "primary":
                raise LLMRateLimitError("429 限流")
            return _DemoModel(value="ok")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        # _record_429 写入失败静默丢弃，退避状态缺失但调用链继续切 backup
        assert chain.call_with_fallback("prompt", _DemoModel).value == "ok"


class TestCallProviderErrorMapping:
    """_call_provider 异常区分：429→LLMRateLimitError、5xx→LLMServerError、4xx→LLMExtractionError。"""

    def _install_fake_client(self, monkeypatch, exc):
        import instructor

        class _FakeCompletions:
            def create(self, **kwargs):
                raise exc

        fake = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
        monkeypatch.setattr(instructor, "from_openai", lambda *a, **k: fake)
        return fake

    def _provider(self):
        return {"name": "primary", "base_url": "http://test", "api_key": "k", "model": "m"}

    def test_429_maps_to_rate_limit_error(self, tmp_path, monkeypatch):
        import httpx
        from openai import RateLimitError

        chain, _ = _make_chain(tmp_path)
        resp = httpx.Response(429, request=httpx.Request("POST", "http://test"))
        self._install_fake_client(
            monkeypatch, RateLimitError("限流", response=resp, body=None)
        )
        with pytest.raises(LLMRateLimitError):
            chain._call_provider(self._provider(), "p", _DemoModel, 0, 10)

    def test_5xx_maps_to_server_error(self, tmp_path, monkeypatch):
        import httpx
        from openai import APIStatusError

        chain, _ = _make_chain(tmp_path)
        resp = httpx.Response(500, request=httpx.Request("POST", "http://test"))
        self._install_fake_client(
            monkeypatch, APIStatusError("服务不可用", response=resp, body=None)
        )
        with pytest.raises(LLMServerError):
            chain._call_provider(self._provider(), "p", _DemoModel, 0, 10)

    def test_4xx_stays_generic_extraction_error(self, tmp_path, monkeypatch):
        import httpx
        from openai import APIStatusError

        chain, _ = _make_chain(tmp_path)
        resp = httpx.Response(400, request=httpx.Request("POST", "http://test"))
        self._install_fake_client(
            monkeypatch, APIStatusError("请求错误", response=resp, body=None)
        )
        with pytest.raises(LLMExtractionError) as exc_info:
            chain._call_provider(self._provider(), "p", _DemoModel, 0, 10)
        assert type(exc_info.value) is LLMExtractionError


class TestTranslateProviderExc:
    """_translate_provider_exc 统一映射（审查②去重 helper，合成异常类隔离 SDK）。"""

    @pytest.fixture()
    def _sdk(self):
        class _Timeout(Exception):
            pass

        class _RateLimit(Exception):
            pass

        class _Status(Exception):
            def __init__(self, status_code: int):
                super().__init__(f"status {status_code}")
                self.status_code = status_code

        class _Conn(Exception):
            pass

        return (_Timeout, _RateLimit, _Status, _Conn)

    def _translate(self, sdk, exc, **kwargs):
        return llm_provider_module._translate_provider_exc("p1", 30, exc, sdk, **kwargs)

    def test_timeout_maps_first(self, _sdk):
        err = self._translate(_sdk, _sdk[0]("t"))
        assert isinstance(err, LLMTimeoutError)
        assert err.outcome == llm_provider_module._OUTCOME_TIMEOUT

    def test_rate_limit(self, _sdk):
        err = self._translate(_sdk, _sdk[1]("r"))
        assert isinstance(err, LLMRateLimitError)
        assert err.outcome == llm_provider_module._OUTCOME_RATE_LIMITED

    def test_status_5xx_server_error(self, _sdk):
        err = self._translate(_sdk, _sdk[2](503))
        assert isinstance(err, LLMServerError)
        assert err.outcome == llm_provider_module._OUTCOME_SERVER_ERROR

    def test_status_4xx_http_4xx(self, _sdk):
        err = self._translate(_sdk, _sdk[2](403))
        assert type(err) is LLMExtractionError
        assert err.outcome == llm_provider_module._OUTCOME_HTTP_4XX

    def test_connection_error(self, _sdk):
        err = self._translate(_sdk, _sdk[3]("c"))
        assert type(err) is LLMExtractionError
        assert err.outcome == llm_provider_module._OUTCOME_CONNECTION_ERROR

    def test_generic_without_validation_aware(self, _sdk):
        err = self._translate(_sdk, RuntimeError("x"), validation_aware=False)
        assert type(err) is LLMExtractionError
        assert err.outcome == llm_provider_module._OUTCOME_EXTRACTION_ERROR

    def test_validation_error_aware(self, _sdk):
        class _M(BaseModel):
            v: int

        with pytest.raises(ValidationError) as ei:
            _M.model_validate({"v": "abc"})
        err = self._translate(_sdk, ei.value)
        assert err.outcome == llm_provider_module._OUTCOME_VALIDATION_ERROR


class TestAnthropicProtocol:
    """protocol: anthropic 分支（2026-08-30，z.ai Coding Plan 接入）。

    覆盖：client 构建（from_anthropic + 模式映射 + 缓存隔离）、调用路径
    （messages.create）、异常映射与 OpenAI 路径同语义、链级协议混排。
    """

    def _anthropic_provider(self, **extra):
        provider = {
            "name": "zai",
            "base_url": "https://api.z.ai/api/anthropic",
            "api_key": "k-zai",
            "model": "glm-5.3-flash",
            "protocol": "anthropic",
        }
        provider.update(extra)
        return provider

    def _install_fake_anthropic(self, monkeypatch, result=None, exc=None):
        import instructor

        calls: list[dict] = []

        class _FakeMessages:
            def create(self, **kwargs):
                calls.append(kwargs)
                if exc is not None:
                    raise exc
                return result if result is not None else _DemoModel(value="ok")

        fake = SimpleNamespace(messages=_FakeMessages())
        captured: dict = {}

        def fake_from_anthropic(client, mode=None, **kw):
            captured["mode"] = mode
            return fake

        monkeypatch.setattr(instructor, "from_anthropic", fake_from_anthropic)
        return fake, captured, calls

    def test_build_client_uses_from_anthropic_tools_mode(self, tmp_path, monkeypatch):
        import instructor

        _, captured, _ = self._install_fake_anthropic(monkeypatch)
        client = llm_provider_module._build_client(self._anthropic_provider(), 10)
        assert client is not None
        assert captured["mode"] == instructor.Mode.ANTHROPIC_TOOLS

    def test_build_client_json_mode_when_no_function_calling(self, tmp_path, monkeypatch):
        import instructor

        _, captured, _ = self._install_fake_anthropic(monkeypatch)
        provider = self._anthropic_provider(supports_function_calling=False)
        llm_provider_module._build_client(provider, 10)
        assert captured["mode"] == instructor.Mode.ANTHROPIC_JSON

    def test_client_cache_isolated_by_protocol(self, monkeypatch):

        self._install_fake_anthropic(monkeypatch)
        anthropic_provider = self._anthropic_provider()
        openai_provider = {k: v for k, v in anthropic_provider.items() if k != "protocol"}
        c1 = llm_provider_module._build_client(anthropic_provider, 10)
        c2 = llm_provider_module._build_client(openai_provider, 10)
        assert c1 is not c2  # 同 base_url 不同协议不共享缓存

    def test_call_provider_routes_to_messages_create(self, tmp_path, monkeypatch):
        chain, _ = _make_chain(tmp_path)
        fake, _, calls = self._install_fake_anthropic(monkeypatch)
        result = chain._call_provider(
            self._anthropic_provider(), "p", _DemoModel, 0, 10, system_prompt="sys"
        )
        assert result.value == "ok"
        assert len(calls) == 1
        assert calls[0]["model"] == "glm-5.3-flash"
        assert calls[0]["response_model"] is _DemoModel
        assert fake is not None

    def test_fallback_switches_from_anthropic_to_openai(self, tmp_path, monkeypatch, _fake_store):
        path = tmp_path / "llm.yaml"
        _write_config(path, [
            self._anthropic_provider(priority=1),
            {"name": "backup", "priority": 2, "api_key": "k2", "enabled": True},
        ])
        chain = LLMProviderChain(config_path=path)

        def fake_call(provider, prompt, response_model, max_retries, timeout, system_prompt=None):
            if provider.get("protocol") == "anthropic":
                raise LLMRateLimitError("429 限流")
            return _DemoModel(value="ok")

        monkeypatch.setattr(chain, "_call_provider", fake_call)
        assert chain.call_with_fallback("prompt", _DemoModel).value == "ok"

    def test_429_maps_to_rate_limit_error(self, tmp_path, monkeypatch):
        import httpx

        from anthropic import RateLimitError

        chain, _ = _make_chain(tmp_path)
        resp = httpx.Response(429, request=httpx.Request("POST", "http://test"))
        self._install_fake_anthropic(
            monkeypatch, exc=RateLimitError("限流", response=resp, body=None)
        )
        with pytest.raises(LLMRateLimitError):
            chain._call_provider(self._anthropic_provider(), "p", _DemoModel, 0, 10)

    def test_5xx_maps_to_server_error(self, tmp_path, monkeypatch):
        import httpx

        from anthropic import APIStatusError

        chain, _ = _make_chain(tmp_path)
        resp = httpx.Response(500, request=httpx.Request("POST", "http://test"))
        self._install_fake_anthropic(
            monkeypatch, exc=APIStatusError("服务不可用", response=resp, body=None)
        )
        with pytest.raises(LLMServerError):
            chain._call_provider(self._anthropic_provider(), "p", _DemoModel, 0, 10)

    def test_4xx_stays_generic_extraction_error(self, tmp_path, monkeypatch):
        import httpx

        from anthropic import APIStatusError

        chain, _ = _make_chain(tmp_path)
        resp = httpx.Response(400, request=httpx.Request("POST", "http://test"))
        self._install_fake_anthropic(
            monkeypatch, exc=APIStatusError("请求错误", response=resp, body=None)
        )
        with pytest.raises(LLMExtractionError) as exc_info:
            chain._call_provider(self._anthropic_provider(), "p", _DemoModel, 0, 10)
        assert type(exc_info.value) is LLMExtractionError

    def test_timeout_maps_to_timeout_error(self, tmp_path, monkeypatch):
        import httpx

        from anthropic import APITimeoutError

        chain, _ = _make_chain(tmp_path)
        self._install_fake_anthropic(
            monkeypatch, exc=APITimeoutError(httpx.Request("POST", "http://test"))
        )
        with pytest.raises(LLMTimeoutError):
            chain._call_provider(self._anthropic_provider(), "p", _DemoModel, 0, 10)


class TestAnthropicHealthCheck:
    """protocol: anthropic 的健康探针走 /v1/models + x-api-key + anthropic-version。"""

    def _fake_response(self, status):
        class _R:
            def __init__(self, s):
                self.status = s

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _R(status)

    def test_probe_url_and_headers(self, monkeypatch, _fake_store):
        captured: dict = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            return self._fake_response(200)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        provider = {
            "name": "zai",
            "base_url": "https://api.z.ai/api/anthropic",
            "api_key": "k-zai",
            "protocol": "anthropic",
        }
        assert check_provider_health(provider) is True
        assert captured["url"] == "https://api.z.ai/api/anthropic/v1/models"
        assert captured["headers"]["x-api-key"] == "k-zai"
        assert captured["headers"]["anthropic-version"] == "2023-06-01"

    def test_non_200_marks_unhealthy(self, monkeypatch, _fake_store):
        monkeypatch.setattr(
            "urllib.request.urlopen", lambda req, timeout: self._fake_response(404)
        )
        provider = {
            "name": "zai",
            "base_url": "https://api.z.ai/api/anthropic",
            "api_key": "k-zai",
            "protocol": "anthropic",
        }
        assert check_provider_health(provider) is False
        assert _fake_store.data["llm:health:zai"] == "0"
