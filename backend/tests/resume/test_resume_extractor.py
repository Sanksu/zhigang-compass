"""简历抽取器单元测试（设计文档 §8.3）。

覆盖：LLM 路径（结果经技能黑名单过滤）、LLM 失败降级规则抽取、
过短文本返回空、空白文本不触发 LLM 调用。
"""

from app.services.extraction.llm_provider import LLMExtractionError
from app.services.resume.extractor import ResumeExtractor
from app.services.resume.schemas import (
    ResumeExtractionResult,
    ResumeProject,
    ResumeSkill,
)


class _FakeLLM:
    """固定返回抽取结果的假 LLM 链。"""

    def __init__(self, result: ResumeExtractionResult):
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
    def test_llm_path_filters_stopword_skills(self):
        raw = ResumeExtractionResult(
            skills=[
                ResumeSkill(name="Python", proficiency=3),
                ResumeSkill(name="金融", proficiency=2),  # 行业词，幻觉技能
            ],
            projects=[ResumeProject(name="推荐系统", stack=["Python", "Redis"])],
        )
        fake = _FakeLLM(raw)
        out = ResumeExtractor(llm=fake).extract("这是一份足够长的简历文本，用于验证抽取流程")
        assert fake.calls == 1
        assert [s.name for s in out.skills] == ["Python"]  # "金融" 被黑名单剔除
        assert out.skills[0].unmapped is False  # 白名单技能不标记 unmapped
        assert out.projects[0].name == "推荐系统"

    def test_llm_path_normalizes_skills(self):
        """技能别名归一化 + 去重（与 JD 抽取同口径）：Golang→Go、Spring→Spring Boot。"""
        raw = ResumeExtractionResult(
            skills=[
                ResumeSkill(name="Golang", proficiency=3),
                ResumeSkill(name="golang", proficiency=2),  # 别名重复
                ResumeSkill(name="Spring", proficiency=2),
                ResumeSkill(name="Vue", proficiency=2),
            ],
        )
        out = ResumeExtractor(llm=_FakeLLM(raw)).extract("这是一份足够长的简历文本，用于验证抽取流程")
        assert [s.name for s in out.skills] == ["Go", "Spring Boot", "Vue.js"]
        # 归一化后保留首次熟练度；白名单标准名不标记 unmapped
        assert out.skills[0].proficiency == 3
        assert all(not s.unmapped for s in out.skills)

    def test_non_whitelist_skill_kept_with_unmapped_flag(self):
        """白名单外的长尾技能保留但标记 unmapped（设计文档 8.4 走人工确认）。"""
        raw = ResumeExtractionResult(skills=[ResumeSkill(name="数据仓库", proficiency=2)])
        out = ResumeExtractor(llm=_FakeLLM(raw)).extract("这是一份足够长的简历文本，用于验证抽取流程")
        assert [s.name for s in out.skills] == ["数据仓库"]
        assert out.skills[0].unmapped is True

    def test_stopword_skill_never_revived_by_unmapped(self):
        """黑名单词（行业/业务领域）仍被剔除，不因 unmapped 机制复活。"""
        raw = ResumeExtractionResult(skills=[ResumeSkill(name="金融", proficiency=2)])
        out = ResumeExtractor(llm=_FakeLLM(raw)).extract("这是一份足够长的简历文本，用于验证抽取流程")
        assert out.skills == []

    def test_llm_failure_falls_back_to_rule_based(self):
        extractor = ResumeExtractor(llm=_FailingLLM())
        text = (
            "[NAME]\n"
            "求职意向：Python 后端开发\n"
            "教育背景：本科\n"
            "5年工作经验，熟悉 Python、MySQL、Docker 容器化部署\n"
            "项目：电商平台订单系统"
        )
        out = extractor.extract(text)
        names = {s.name for s in out.skills}
        assert {"Python", "MySQL", "Docker"}.issubset(names)
        assert out.education_level == "本科"
        assert out.total_years == 5.0

    def test_short_text_returns_empty(self):
        extractor = ResumeExtractor(llm=_FakeLLM(ResumeExtractionResult()))
        out = extractor.extract("太短")
        assert out.skills == []
        assert out.education_level == ""

    def test_blank_text_returns_empty(self):
        fake = _FakeLLM(ResumeExtractionResult())
        extractor = ResumeExtractor(llm=fake)
        out = extractor.extract("")
        assert out.skills == []
        assert fake.calls == 0  # 过短文本不触发 LLM 调用

    def test_rule_based_education_priority(self):
        extractor = ResumeExtractor(llm=_FailingLLM())
        out = extractor.extract("硕士毕业于 XX 大学，本科就读于 YY 大学")
        assert out.education_level == "硕士"  # 按从高到低取首个命中


class TestSoftSkillInference:
    """LLM 推断软技能全链路（设计文档 9.2 节：标 low_confidence，匹配降权 ×0.5）。"""

    def test_soft_skills_merged_with_low_confidence(self):
        raw = ResumeExtractionResult(
            skills=[ResumeSkill(name="Python", proficiency=3)],
            soft_skills=["团队协作", "沟通能力", "项目管理"],
        )
        out = ResumeExtractor(llm=_FakeLLM(raw)).extract("这是一份足够长的简历文本，用于验证抽取流程")
        by_name = {s.name: s for s in out.skills}
        assert by_name["Python"].low_confidence is False  # 显式技能不降权
        assert by_name["团队协作"].low_confidence is True
        assert by_name["沟通能力"].low_confidence is True
        assert by_name["项目管理"].low_confidence is True

    def test_non_whitelist_soft_skill_dropped(self):
        raw = ResumeExtractionResult(soft_skills=["体力好"])  # 白名单外
        out = ResumeExtractor(llm=_FakeLLM(raw)).extract("这是一份足够长的简历文本，用于验证抽取流程")
        assert out.skills == []

    def test_explicit_skill_wins_over_inferred(self):
        """文本直述的软技能（显式）不被推断项覆盖，保持不降权。"""
        raw = ResumeExtractionResult(
            skills=[ResumeSkill(name="项目管理", proficiency=3)],
            soft_skills=["项目管理"],
        )
        out = ResumeExtractor(llm=_FakeLLM(raw)).extract("这是一份足够长的简历文本，用于验证抽取流程")
        assert len(out.skills) == 1
        assert out.skills[0].low_confidence is False
        assert out.skills[0].proficiency == 3

    def test_rule_based_path_has_no_soft_skills(self):
        """规则兜底不产生 soft_skills（软技能仅走 LLM 推断通道）。"""
        extractor = ResumeExtractor(llm=_FailingLLM())
        out = extractor.extract("[NAME]\n5年经验，熟悉 Python，项目：团队协作完成推荐系统开发")
        assert out.soft_skills == []
