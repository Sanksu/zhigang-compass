"""LLM Provider 链（M3 参考实现，算法红线：须算法岗张恺天逐行把关）。

对齐设计文档 §6.5 + AGENTS.md §6.4：
- 读取 `configs/llm_providers.yaml`（单一事实源），provider 为任意 OpenAI 兼容 API，
  可自由增删/调序；api_key 由管理员经 /admin/llm 后台填写并持久化到该文件
- 按 priority 升序、enabled 过滤，按顺序依次尝试
- instructor + Pydantic Schema 强校验（幻觉防控第一道防线），校验失败自动重试 1 次
- 同步路由 `call_sync`：单次尝试不重试，超时即抛 `LLMTimeoutError`（同步路由 10s 上限）
- 异步任务 `call_with_fallback`：按优先级依次尝试，单 provider 超时切下一个（30s × N 上限）

未配置 api_key 时抛 `LLMConfigurationError`（LLMExtractionError 子类），
jd_extractor 捕获后降级规则抽取，保证无 key 环境可运行。
"""

from pathlib import Path
from typing import Optional, Type, TypeVar

import yaml
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# 配置路径（相对 backend 根目录，与 admin.py 的 _LLM_CONFIG_PATH 一致）
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "llm_providers.yaml"

# 同步/异步单 provider 超时（秒，对齐设计文档 §6.5）
SYNC_TIMEOUT_SECONDS = 10
ASYNC_TIMEOUT_SECONDS = 30
# 幻觉防控第一道防线：schema 校验失败重试次数（设计文档 §6.3）
VALIDATION_RETRIES = 1


class LLMExtractionError(Exception):
    """LLM 抽取失败（超时/格式错误等）。"""


class LLMConfigurationError(LLMExtractionError):
    """未配置可用 provider（无 api_key / 全部禁用 / yaml 缺失）。"""


class LLMTimeoutError(LLMExtractionError):
    """单 provider 调用超时。"""


class LLMProviderChain:
    """多 provider 重试链。

    Args:
        config_path: yaml 配置路径，缺省为 `configs/llm_providers.yaml`（测试可注入）
    """

    def __init__(self, config_path: Optional[Path] = None):
        self._config_path = config_path or _CONFIG_PATH
        self._providers = self._load_providers()
        self._temperature = self._load_temperature()

    def _load_providers(self) -> list[dict]:
        """读取 yaml 并按 priority 升序返回 enabled provider。"""
        if not self._config_path.exists():
            raise LLMConfigurationError(f"LLM 配置缺失: {self._config_path}")
        try:
            data = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise LLMConfigurationError(f"LLM 配置解析失败: {e}") from e
        providers = [
            p for p in data.get("providers", [])
            if isinstance(p, dict)
        ]
        enabled = [p for p in providers if p.get("enabled")]
        return sorted(enabled, key=lambda p: p.get("priority", 99))

    def _load_temperature(self) -> float:
        """结构化输出温度（yaml `structured_output.temperature`，缺省 0.1）。"""
        try:
            data = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
            return float(data.get("structured_output", {}).get("temperature", 0.1))
        except (OSError, yaml.YAMLError, TypeError, ValueError):
            return 0.1

    # ---- 对外接口 ----

    def extract_structured(
        self,
        prompt: str,
        response_model: Type[T],
        max_retries: int = VALIDATION_RETRIES,
        system_prompt: Optional[str] = None,
    ) -> T:
        """抽取接口（jd_extractor 兼容）：走异步语义的重试链。

        Args:
            system_prompt: 系统角色提示词（分层 Prompt，§6.2 设计）
        """
        return self.call_with_fallback(
            prompt, response_model, max_retries=max_retries, system_prompt=system_prompt
        )

    def call_sync(self, prompt: str, response_model: Type[T], system_prompt: Optional[str] = None) -> T:
        """同步路由：仅尝试优先级最高的 provider，超时即抛，不重试、不切换。

        供图谱查询/诊断报告等实时路径使用，调用方捕获 `LLMTimeoutError` 返回 503。
        """
        if not self._providers:
            raise LLMConfigurationError("未配置可用 provider（无 api_key 或全部禁用）")
        return self._call_provider(
            self._providers[0], prompt, response_model,
            max_retries=0, timeout=SYNC_TIMEOUT_SECONDS, system_prompt=system_prompt,
        )

    def call_with_fallback(
        self,
        prompt: str,
        response_model: Type[T],
        max_retries: int = VALIDATION_RETRIES,
        system_prompt: Optional[str] = None,
    ) -> T:
        """异步/批量路由：按优先级依次尝试，任一失败（超时/限流/5xx/连接/校验）切下一个。"""
        if not self._providers:
            raise LLMConfigurationError("未配置可用 provider（无 api_key 或全部禁用）")
        failures = []
        for provider in self._providers:
            try:
                return self._call_provider(
                    provider, prompt, response_model, max_retries,
                    ASYNC_TIMEOUT_SECONDS, system_prompt,
                )
            except LLMExtractionError as e:
                # 捕获父类：429/5xx/连接错误均包装为 LLMExtractionError，
                # 任一失败都继续尝试下一个 provider（§6.5 重试链语义）
                failures.append(str(e))
                continue
        raise LLMExtractionError("所有 provider 均失败: " + " | ".join(failures))

    # ---- 内部 ----

    @staticmethod
    def _build_messages(prompt: str, system_prompt: Optional[str]) -> list[dict]:
        """分层 Prompt（§6.2）：可选 system 角色 + user 任务输入。"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _call_provider(
        self,
        provider: dict,
        prompt: str,
        response_model: Type[T],
        max_retries: int,
        timeout: int,
        system_prompt: Optional[str] = None,
    ) -> T:
        """调用单个 provider 的结构化抽取（instructor 强校验）。

        外部 API 调用是系统边界：任何调用异常都包装为 LLMExtractionError 子类，
        使重试链可继续尝试下一个 provider。
        """
        import instructor
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            OpenAI,
            RateLimitError,
        )

        api_key = (provider.get("api_key") or "").strip()
        if not api_key:
            raise LLMConfigurationError(f"provider '{provider['name']}' 未配置 api_key")

        client = instructor.from_openai(
            OpenAI(base_url=provider["base_url"], api_key=api_key, timeout=timeout),
            mode=(
                instructor.Mode.TOOLS
                if provider.get("supports_function_calling", True)
                else instructor.Mode.JSON_SCHEMA
            ),
        )
        try:
            return client.chat.completions.create(
                model=provider["model"],
                response_model=response_model,
                messages=self._build_messages(prompt, system_prompt),
                temperature=self._temperature,
                max_retries=max_retries,
                # provider 特定请求参数透传（如 deepseek-v4-flash 关闭思考模式:
                # {"thinking": {"type": "disabled"}}，见 configs/llm_providers.yaml）
                extra_body=provider.get("extra_body") or None,
            )
        except APITimeoutError as e:
            raise LLMTimeoutError(f"provider '{provider['name']}' 超时（{timeout}s）") from e
        except (APIConnectionError, RateLimitError, APIStatusError) as e:
            raise LLMExtractionError(f"provider '{provider['name']}' 调用失败: {e}") from e
        except Exception as e:  # 外部 API/校验异常统一包装，交给重试链
            raise LLMExtractionError(f"provider '{provider['name']}' 调用异常: {e}") from e
