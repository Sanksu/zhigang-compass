"""JDExtractor.extract_batch 集成测试（真实网络请求，需配置有效 api_key）。

实装后本文件对齐产品代码：直接用 `JDExtractor.extract_batch` 编排逻辑，
注入「真实 instructor client」包装的 LLM 链适配器（TOOLS + thinking disabled），
验证批量超时降级与动态长度封顶在真实网络环境下的行为。

⚠️ 使用注意：
- 每次运行会真实调用 LLM API（产生费用/耗时），默认不进入 CI 全量测试
  （pytest 已配置 `-m not integration` 默认排除）
- 运行：`uv run python -m pytest tests/extraction/test_batch_extract_integration.py -m integration -v -s`
- configs/llm_providers.yaml 缺失（CI/全新检出）或无可用 provider 时整体 skip
"""

import time
from pathlib import Path

import pytest
import yaml
from openai import OpenAI
import instructor

from app.services.extraction.jd_extractor import JDExtractor
from app.services.extraction.llm_provider import LLMExtractionError
from app.services.extraction.schemas import JDExtractionBatch, JDExtractionResult


# ============================================================
# 真实 LLM 链适配器（对齐 llm_provider._call_provider 的 client 参数）
# ============================================================

# configs/llm_providers.yaml 为 gitignore 文件（api_key 不入库），CI/全新检出不存在。
# 缺配置时整体 skip（而非 collection error），本文件仅在本地真实联调时运行。
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "llm_providers.yaml"
if _CONFIG_PATH.exists():
    _CONFIG = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    _PROVIDER = next((p for p in _CONFIG.get("providers", []) if p.get("enabled")), None)
else:
    _PROVIDER = None

pytestmark = pytest.mark.skipif(
    _PROVIDER is None,
    reason="缺少 configs/llm_providers.yaml（gitignore），集成测试需本地真实 api_key",
)


class _RealLLMAdapter:
    """实现 LLMProviderChain.extract_structured 接口，真实调用 API。

    - response_model 为 JDExtractionBatch → 批量（一次 N 条）；否则单条
    - timeout 取自 kwargs（batch_timeout 由 extract_batch 透传），单条走默认 60s
    - 异常包装对齐 _call_provider：超时/API 异常统一转 LLMExtractionError 子类
    """

    DEFAULT_TIMEOUT = 60.0

    def __init__(self, batch_timeout: float = 90.0, max_tokens: int | None = None):
        self.batch_timeout = batch_timeout
        self.max_tokens = max_tokens
        self.batch_calls = 0
        self.single_calls = 0

    def _client(self, timeout: float):
        return instructor.from_openai(
            OpenAI(
                base_url=_PROVIDER["base_url"],
                api_key=_PROVIDER["api_key"],
                timeout=timeout,
            ),
            mode=(
                instructor.Mode.TOOLS
                if _PROVIDER.get("supports_function_calling", True)
                else instructor.Mode.JSON_SCHEMA
            ),
        )

    def extract_structured(self, prompt, response_model, **kwargs):
        # 批量请求用透传的 batch_timeout（可能极小以触发超时）；单条降级请求
        # 必须用默认超时，否则极小 batch_timeout 会让逐条也超时、全部失败
        if response_model is JDExtractionBatch:
            timeout = kwargs.get("timeout") or self.batch_timeout
        else:
            timeout = self.DEFAULT_TIMEOUT
        client = self._client(timeout)
        try:
            result = client.chat.completions.create(
                model=_PROVIDER["model"],
                response_model=response_model,
                messages=[{"role": "user", "content": prompt}],
                max_retries=0,
                max_tokens=self.max_tokens,
                extra_body=_PROVIDER.get("extra_body") or None,
            )
            if response_model is JDExtractionBatch:
                self.batch_calls += 1
            else:
                self.single_calls += 1
            return result
        except LLMExtractionError:
            raise
        except Exception as e:
            raise LLMExtractionError(f"调用异常: {e}") from e


# ============================================================
# 集成测试
# ============================================================

_SHORT_TEXTS = [
    "JD 文本 A：招聘 Python 开发，精通 Django、Flask",
    "JD 文本 B：招聘 Java 后端，熟悉 Spring Boot、Redis",
    "JD 文本 C：招聘前端工程师，熟练 React、TypeScript",
    "JD 文本 D：招聘 Go 开发，了解 Kubernetes、Docker",
]

_LONG_TEXTS = [
    "JD 文本 A：" + "要求精通 Python 与数据清洗，熟悉 pandas 与 numpy，具备 3 年以上经验。" * 40,
    "JD 文本 B：" + "招聘资深 Java 工程师，负责高并发系统设计，熟悉 JVM 调优。" * 40,
    "JD 文本 C：" + "前端岗位：负责可视化大屏，掌握 ECharts 与 WebGL。" * 40,
]


@pytest.mark.integration
class TestRealBatchTimeoutFallback:
    def test_real_short_timeout_triggers_fallback(self):
        """真实网络：极小 batch_timeout → 批量必然超时 → 降级逐条（编排不崩溃）。"""
        # 极小超时保证真实请求必然超时（网络往返本身 > 0.3s）
        adapter = _RealLLMAdapter(batch_timeout=0.3)
        extractor = JDExtractor(llm=adapter)

        t0 = time.time()
        out = extractor.extract_batch(_SHORT_TEXTS, batch_size=2, batch_timeout=0.3)
        elapsed = time.time() - t0
        # 4 条分 2 批 → 批量两次全超时 → 逐条 4 次
        assert len(out) == 4, f"结果条数应为 4，实际 {len(out)}"
        for r in out:
            assert r.position_name, f"存在空岗位名结果: {r.model_dump()}"
        print(
            f"[集成·超时降级] 真实网络耗时 {elapsed:.1f}s，批量调用 {adapter.batch_calls} 次、"
            f"逐条 {adapter.single_calls} 次，岗位名={[r.position_name for r in out]}"
        )

    def test_real_batch_success_path(self):
        """真实网络对照：正常 batch_timeout → 批量直通，批量调用 >0、逐条 =0。"""
        adapter = _RealLLMAdapter(batch_timeout=90.0)
        extractor = JDExtractor(llm=adapter)

        t0 = time.time()
        out = extractor.extract_batch(_SHORT_TEXTS, batch_size=2, batch_timeout=90.0)
        elapsed = time.time() - t0
        assert len(out) == 4
        for r in out:
            assert r.position_name
        assert adapter.batch_calls > 0
        assert adapter.single_calls == 0
        print(
            f"[集成·批量直通] 真实网络耗时 {elapsed:.1f}s，批量调用 {adapter.batch_calls} 次，"
            f"岗位名={[r.position_name for r in out]}"
        )


@pytest.mark.integration
class TestRealDynamicBatching:
    def test_real_chars_cap_splits_batches(self):
        """真实网络：长文本 + 大 batch_size + 小 max_batch_chars → 按长度切批。"""
        char_lens = [len(t) for t in _LONG_TEXTS]
        max_chars = max(char_lens)  # 每条刚好 < 上限 → 3 条 3 批
        # 长文本 + 复杂 schema 需足够输出预算，否则 reasoning 耗尽导致字段残缺
        adapter = _RealLLMAdapter(batch_timeout=90.0, max_tokens=4000)
        extractor = JDExtractor(llm=adapter)

        t0 = time.time()
        out = extractor.extract_batch(
            _LONG_TEXTS, batch_size=10, max_batch_chars=max_chars
        )
        elapsed = time.time() - t0
        assert len(out) == 3
        for r in out:
            assert r.position_name
        assert adapter.batch_calls == 3  # 3 批 3 次批量调用
        print(
            f"[集成·动态封顶] 文本长度={char_lens}，封顶={max_chars} 字符，切 3 批；"
            f"真实网络耗时 {elapsed:.1f}s，产出 3 条：{[r.position_name for r in out]}"
        )

    def test_real_chars_cap_within_batch_size(self):
        """真实网络：短文本双约束共存，按 batch_size 切（不超长）。"""
        adapter = _RealLLMAdapter(batch_timeout=90.0)
        extractor = JDExtractor(llm=adapter)

        t0 = time.time()
        out = extractor.extract_batch(
            _SHORT_TEXTS, batch_size=2, max_batch_chars=100_000
        )
        elapsed = time.time() - t0
        assert len(out) == 4
        assert adapter.batch_calls == 2  # 短文本按 2 条/批 → 2 批
        print(
            f"[集成·双约束] 短文本按 2 条/批真实抽取，耗时 {elapsed:.1f}s，"
            f"产出 {len(out)} 条：{[r.position_name for r in out]}"
        )
