"""JD 实体抽取器单元测试（设计文档 §5.2 抽取管线）。

覆盖：LLM 路径（结果经后处理）、LLM 失败降级规则抽取、过短文本返回空。
"""

from unittest.mock import patch

from app.services.extraction.jd_extractor import JDExtractor
from app.services.extraction.llm_provider import LLMConfigurationError, LLMExtractionError
from app.services.extraction.prompts import BATCH_TASK_TEMPLATE, TASK_TEMPLATE
from app.services.extraction.schemas import (
    JDExtractionResult,
    REQUIRESRelation,
    SkillExtracted,
    TypicalScenario,
)


class _FakeLLM:
    """固定返回抽取结果的假 LLM 链。"""

    def __init__(self, result: JDExtractionResult):
        self.result = result
        self.calls = 0
        self.last_kwargs = None

    def extract_structured(self, prompt, response_model, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
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
        out = JDExtractor(llm=fake).extract(
            "这是一个足够长的 JD 文本，用于验证抽取流程，熟悉 Docker"
        )
        assert fake.calls == 1
        # 后处理：后缀清洗 + 大小写去重，requirements 同规则
        # （正文含 Docker 词面，词面守卫不降级）
        assert [s.name for s in out.skills] == ["Docker"]
        assert out.requirements[0].skill_name == "Docker"
        assert out.position_name == "Python 开发工程师"
        # P1-2：LLM 路径 method=llm（默认）
        assert out.method == "llm"

    def test_extract_forwards_timeout(self):
        """extract 的 timeout 参数透传给 provider 链（评测 30s→60s 依赖此转发）。"""
        raw = JDExtractionResult(position_name="Java 开发工程师")
        fake = _FakeLLM(raw)
        out = JDExtractor(llm=fake).extract(
            "这是一个足够长的 JD 文本，用于验证 timeout 透传，熟悉 Java",
            timeout=60,
        )
        assert fake.last_kwargs.get("timeout") == 60
        assert out.position_name == "Java 开发工程师"

    def test_lexical_guard_demotes_not_in_text(self):
        """词面守卫（08-17）：LLM 路径 skills 中正文无词面的技能降级 nice。"""
        raw = JDExtractionResult(
            position_name="",
            skills=[
                SkillExtracted(name="Docker"),
                SkillExtracted(name="Kubernetes"),
            ],
        )
        fake = _FakeLLM(raw)
        out = JDExtractor(llm=fake).extract(
            "这是一个足够长的 JD 文本，用于验证抽取流程，熟悉 Docker 技术"
        )
        assert [s.name for s in out.skills] == ["Docker"]  # Kubernetes 无词面被降级
        assert {r.skill_name: r.necessity for r in out.requirements} == {
            "Kubernetes": "nice"
        }

    def test_lexical_guard_alias_exempt(self):
        """词面守卫别名豁免：正文用缩写（LLM）时技能（大语言模型）保留。"""
        raw = JDExtractionResult(
            position_name="",
            skills=[SkillExtracted(name="大语言模型")],
        )
        fake = _FakeLLM(raw)
        out = JDExtractor(llm=fake).extract(
            "这是一个足够长的 JD 文本，熟悉 LLM 相关技术"
        )
        assert [s.name for s in out.skills] == ["大语言模型"]

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


class TestTypicalScenarios:
    def test_schema_defaults_to_empty_list(self):
        """既有抽取数据缺 typical_scenarios 字段时兼容为默认空列表（无需迁移）。"""
        assert JDExtractionResult(position_name="x").typical_scenarios == []
        assert JDExtractionResult(position_name="x").model_dump()["typical_scenarios"] == []

    def test_schema_accepts_explicit_scenarios(self):
        r = JDExtractionResult(
            position_name="数据平台工程师",
            typical_scenarios=[
                TypicalScenario(name="实时数仓建设", description="Flink 实时计算"),
            ],
        )
        assert r.typical_scenarios[0].name == "实时数仓建设"
        assert r.typical_scenarios[0].description == "Flink 实时计算"

    def test_prompt_templates_render_and_contain_scenario_rule(self):
        """提示词模板可 format（花括号双写不崩）且含典型场景规则。"""
        p1 = TASK_TEMPLATE.format(jd_text="测试 JD 文本")
        assert "typical_scenarios" in p1 and "典型场景" in p1
        p2 = BATCH_TASK_TEMPLATE.format(jd_count=2, jd_texts="jd1\njd2")
        assert "typical_scenarios" in p2 and "典型场景" in p2

    def test_llm_path_preserves_scenarios(self):
        """LLM 返回场景时，抽取结果透传场景字段。"""
        raw = JDExtractionResult(
            position_name="数据平台工程师",
            skills=[SkillExtracted(name="Flink")],
            typical_scenarios=[
                TypicalScenario(name="实时数仓建设", description="Flink 实时计算，支撑业务报表"),
            ],
        )
        with patch.object(_FakeLLM, "extract_structured", return_value=raw):
            extractor = JDExtractor(_FakeLLM(raw))
        out = extractor.extract("这是一个足够长的 JD 文本，用于验证典型场景抽取流程")
        assert out.typical_scenarios == raw.typical_scenarios
        assert out.typical_scenarios[0].name == "实时数仓建设"
