"""抽取后处理单元测试（设计文档 §5.2）。

覆盖中文后缀清洗、别名归一化、去重、requirements 与 skills 同规则。
"""

from app.services.extraction.post_processor import (
    clean_skill_name,
    dedup_skills,
    post_process,
)
from app.services.extraction.schemas import (
    JDExtractionResult,
    REQUIRESRelation,
    SkillExtracted,
)


class TestCleanSkillName:
    def test_removes_chinese_suffix(self):
        assert clean_skill_name("Python 开发工程师") == "Python 开发"
        assert clean_skill_name("Docker 技术") == "Docker"
        assert clean_skill_name("数据平台") == "数据"

    def test_no_suffix_unchanged(self):
        assert clean_skill_name("Python") == "Python"
        assert clean_skill_name("MySQL") == "MySQL"

    def test_empty_input(self):
        assert clean_skill_name("") == ""
        assert clean_skill_name("   ") == ""

    def test_soft_skill_preserved(self):
        # 软技能白名单整体跳过后缀清洗："项目管理"不以"管理"为后缀退化
        assert clean_skill_name("项目管理") == "项目管理"
        assert clean_skill_name("产品设计") == "产品设计"

    def test_microservice_suffix_preserved(self):
        # 微服务是完整技能词，不能被"服务"后缀剥成"微"（历史 bug 回归测试）
        assert clean_skill_name("微服务") == "微服务"
        assert clean_skill_name("微服务架构") == "微服务"
        assert clean_skill_name("云原生") == "云原生"


class TestDedupSkills:
    def test_case_insensitive_dedup_keeps_first(self):
        skills = [
            SkillExtracted(name="Python"),
            SkillExtracted(name="python"),
            SkillExtracted(name="Python"),
        ]
        names = [s.name for s in dedup_skills(skills)]
        assert names == ["Python"]


class TestPostProcess:
    def test_normalize_clean_dedup_full_pipeline(self):
        result = JDExtractionResult(
            position_name="",
            skills=[
                SkillExtracted(name="Docker 技术"),
                SkillExtracted(name="docker 技术"),
                SkillExtracted(name="Go"),
            ],
            requirements=[
                REQUIRESRelation(skill_name="Docker 技术", necessity="must"),
                REQUIRESRelation(skill_name="docker 技术", necessity="must"),
                REQUIRESRelation(skill_name="Go", necessity="nice"),
            ],
        )
        out = post_process(result)
        assert [s.name for s in out.skills] == ["Docker", "Go"]
        # requirements 与 skills 同规则清洗 + 按 (技能, 必要性) 去重
        assert [(r.skill_name, r.necessity) for r in out.requirements] == [
            ("Docker", "must"),
            ("Go", "nice"),
        ]

    def test_skill_and_requirement_same_rule(self):
        """requirements 中出现的技能名清洗后与 skills 同名（保证入图对齐）。"""
        result = JDExtractionResult(
            position_name="",
            skills=[SkillExtracted(name="Kubernetes 技术")],
            requirements=[REQUIRESRelation(skill_name="Kubernetes 技术", necessity="must")],
        )
        out = post_process(result)
        assert out.skills[0].name == out.requirements[0].skill_name == "Kubernetes"

    def test_soft_skills_filtered_to_whitelist(self):
        """soft_skills 仅保留岗位本体白名单 + 去重（LLM 越界输出在此拦截）。"""
        result = JDExtractionResult(
            position_name="",
            soft_skills=["团队协作", "沟通能力", "团队协作", "领导力", "体力好"],
        )
        out = post_process(result)
        assert out.soft_skills == ["团队协作", "沟通能力", "领导力"]

    def test_soft_skill_normalized_before_filter(self):
        """软技能经别名归一化后命中白名单（无别名则原样）。"""
        result = JDExtractionResult(position_name="", soft_skills=["团队协作"])
        out = post_process(result)
        assert out.soft_skills == ["团队协作"]
