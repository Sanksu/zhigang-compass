"""JD 实体抽取器单元测试（设计文档 §5.2 抽取管线）。

覆盖：LLM 路径（结果经后处理）、LLM 失败降级规则抽取、过短文本返回空。
"""

from unittest.mock import patch

from app.services.extraction.jd_extractor import JDExtractor
from app.services.extraction.llm_provider import LLMConfigurationError, LLMExtractionError
from app.services.extraction.schemas import (
    JDExtractionResult,
    REQUIRESRelation,
    SkillExtracted,
)


class _FakeLLM:
    """固定返回抽取结果的假 LLM 链。"""

    def __init__(self, result: JDExtractionResult):
        self.result = result
        self.calls = 0

    def extract_structured(self, prompt, response_model, **kwargs):
        self.calls += 1
        return self.result


class _FailingLLM:
    """恒抛错的假 LLM 链（触发规则抽取兜底）。"""

    def extract_structured(self, prompt, response_model, **kwargs):
        raise LLMExtractionError("provider 全挂")


class TestExtract:
    def test_llm_path_posts_processes(self):
        raw = JDExtractionResult(
            position_name="Python 开发工程师",
            skills=[
                SkillExtracted(name="Docker 技术"),
                SkillExtracted(name="docker 技术"),
            ],
            requirements=[REQUIRESRelation(skill_name="Docker 技术", necessity="must")],
        )
        fake = _FakeLLM(raw)
        out = JDExtractor(llm=fake).extract("这是一个足够长的 JD 文本，用于验证抽取流程")
        assert fake.calls == 1
        # 后处理：后缀清洗 + 大小写去重，requirements 同规则
        assert [s.name for s in out.skills] == ["Docker"]
        assert out.requirements[0].skill_name == "Docker"
        assert out.position_name == "Python 开发工程师"
        # P1-2：LLM 路径 method=llm（默认）
        assert out.method == "llm"

    def test_llm_failure_falls_back_to_rule_based(self):
        extractor = JDExtractor(llm=_FailingLLM())
        jd = (
            "Python 后端开发工程师\n"
            "负责核心业务系统后端开发，熟悉 Python、MySQL、Redis、Docker 容器化部署"
        )
        out = extractor.extract(jd)
        names = {s.name for s in out.skills}
        # 规则兜底按白名单扫描，上述技能均在白名单
        assert {"Python", "MySQL", "Redis", "Docker"}.issubset(names)
        # requirements 与 skills 一一对应（规则抽取双写）
        assert {r.skill_name for r in out.requirements} == names
        # P6：兜底只做文本扫描、无语义判断，不能断言"必备"——全标 nice，
        # 避免 LLM 不可用时 must_count 虚高把低频技能推成 must
        assert all(r.necessity == "nice" for r in out.requirements)
        # 岗位名取首行短标题
        assert out.position_name == "Python 后端开发工程师"
        # P1-2：规则兜底标记 method=rule，供下游识别低置信数据
        assert out.method == "rule"

    def test_short_text_returns_empty(self):
        extractor = JDExtractor(llm=_FakeLLM(JDExtractionResult(position_name="")))
        out = extractor.extract("太短")
        assert out.position_name == ""
        assert out.skills == []

    def test_blank_text_returns_empty(self):
        fake = _FakeLLM(JDExtractionResult(position_name=""))
        extractor = JDExtractor(llm=fake)
        out = extractor.extract("")
        assert out.position_name == ""
        assert fake.calls == 0  # 过短文本不触发 LLM 调用

    def test_no_llm_config_falls_back_to_rule_based(self):
        """LLM 配置缺失（构造期即抛 LLMConfigurationError）：无参构造降级规则兜底。

        与 ResumeExtractor 同口径：__init__ 捕获 LLMConfigurationError，self._llm = None，
        extract 时直接走 _rule_based_extract，不触发 LLM 调用。
        """
        with patch(
            "app.services.extraction.jd_extractor.LLMProviderChain",
            side_effect=LLMConfigurationError("配置缺失"),
        ):
            extractor = JDExtractor()
        assert extractor._llm is None
        jd = (
            "Python 后端开发工程师\n"
            "负责核心业务系统后端开发，熟悉 Python、MySQL、Redis、Docker 容器化部署"
        )
        out = extractor.extract(jd)
        names = {s.name for s in out.skills}
        assert {"Python", "MySQL", "Redis", "Docker"}.issubset(names)
        assert out.position_name == "Python 后端开发工程师"

    def test_no_llm_config_short_text_returns_empty(self):
        """LLM 未配置时，过短文本仍返回空（不触发规则兜底）。"""
        with patch(
            "app.services.extraction.jd_extractor.LLMProviderChain",
            side_effect=LLMConfigurationError("配置缺失"),
        ):
            extractor = JDExtractor()
        out = extractor.extract("太短")
        assert out.position_name == ""
        assert out.skills == []
