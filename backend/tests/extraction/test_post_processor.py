"""抽取后处理单元测试（设计文档 §5.2）。

覆盖中文后缀清洗、别名归一化、去重、requirements 与 skills 同规则。
"""

from app.services.extraction.post_processor import (
    canonical_skill_name,
    clean_skill_name,
    dedup_skills,
    post_process,
)
from app.services.extraction.schemas import (
    JDExtractionResult,
    REQUIRESRelation,
    SkillExtracted,
)


class TestCanonicalSkillName:
    """canonical_skill_name：别名归一 → 后缀清洗，各链路统一口径。"""

    def test_alias_normalization(self):
        # 别名命中（含大小写变体）→ 标准名
        assert canonical_skill_name("Golang") == "Go"
        assert canonical_skill_name("vue") == "Vue.js"
        assert canonical_skill_name("spring") == "Spring Boot"

    def test_suffix_clean(self):
        assert canonical_skill_name("Docker 技术") == "Docker"
        assert canonical_skill_name("数据平台") == "数据"

    def test_strip_whitespace(self):
        assert canonical_skill_name("  Redis  ") == "Redis"

    def test_compound_strip_then_alias(self):
        # 剥修饰词后再查别名（与抽取管线一致，不停在中间碎片）
        assert canonical_skill_name("mybatis-plus框架") == "MyBatis"
        assert canonical_skill_name("Vue3框架") == "Vue.js"

    def test_whitelist_word_preserved(self):
        # 白名单词整体保护，不被后缀剥成泛词碎片
        assert canonical_skill_name("操作系统") == "操作系统"
        assert canonical_skill_name("项目管理") == "项目管理"

    def test_no_lowercase(self):
        # canonical 不含大小写归一，保留白名单标准写法
        assert canonical_skill_name("Redis") == "Redis"
        assert canonical_skill_name("Echarts") == "ECharts"


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

    def test_whitelist_words_preserved(self):
        # P1-2 白名单词整体保护：不以中文后缀退化成泛词碎片
        assert clean_skill_name("操作系统") == "操作系统"
        assert clean_skill_name("嵌入式开发") == "嵌入式开发"
        assert clean_skill_name("自动化测试") == "自动化测试"
        assert clean_skill_name("计算机网络") == "计算机网络"
        assert clean_skill_name("消息队列") == "消息队列"


class TestStopwordInterception:
    """P1-2 泛词停用拦截：JD 高频泛词被 LLM 误抽为技能时在此剔除。"""

    def test_generic_fragments_filtered(self):
        result = JDExtractionResult(
            position_name="",
            skills=[
                SkillExtracted(name="系统"),
                SkillExtracted(name="前端"),
                SkillExtracted(name="操作"),
                SkillExtracted(name="数据处理"),
            ],
        )
        out = post_process(result)
        assert out.skills == []

    def test_whitelist_word_never_intercepted(self):
        # 08-14 迭代：SKILL_STOPWORDS 优先于白名单保护（is_noise_skill 顺序调整），
        # 基础词"操作系统"停用后判噪过滤；路由依赖词（计算机视觉等）仍保护
        result = JDExtractionResult(
            position_name="",
            skills=[SkillExtracted(name="操作系统"), SkillExtracted(name="计算机视觉")],
        )
        out = post_process(result)
        assert [s.name for s in out.skills] == ["计算机视觉"]

    def test_requirement_fragment_filtered(self):
        result = JDExtractionResult(
            position_name="",
            requirements=[REQUIRESRelation(skill_name="监控", necessity="must")],
        )
        out = post_process(result)
        assert out.requirements == []


class TestDedupSkills:
    def test_case_insensitive_dedup_keeps_first(self):
        skills = [
            SkillExtracted(name="Python"),
            SkillExtracted(name="python"),
            SkillExtracted(name="Python"),
        ]
        names = [s.name for s in dedup_skills(skills)]
        assert names == ["Python"]


class TestNormalizeAfterClean:
    """P1 归一化顺序：剥后缀后再查别名，快照存标准技能名（非中间碎片）。"""

    def test_alias_applied_after_suffix_strip(self):
        # "mybatis-plus框架" 剥"框架"后须再经别名归并，而不是停在 "mybatis-plus"
        result = JDExtractionResult(
            position_name="",
            skills=[SkillExtracted(name="mybatis-plus框架")],
        )
        out = post_process(result)
        assert [s.name for s in out.skills] == ["MyBatis"]

    def test_vue3_framework_to_vue_js(self):
        result = JDExtractionResult(
            position_name="",
            requirements=[REQUIRESRelation(skill_name="Vue3框架", necessity="must")],
        )
        out = post_process(result)
        assert out.requirements[0].skill_name == "Vue.js"

    def test_golang_dev_to_go(self):
        result = JDExtractionResult(
            position_name="",
            skills=[SkillExtracted(name="Golang 开发")],
        )
        out = post_process(result)
        assert [s.name for s in out.skills] == ["Go"]

    def test_springboot_framework_to_spring_boot(self):
        result = JDExtractionResult(
            position_name="",
            skills=[SkillExtracted(name="SpringBoot框架")],
        )
        out = post_process(result)
        assert [s.name for s in out.skills] == ["Spring Boot"]


class TestSkillModifierCompoundWords:
    """P2 技能+修饰词组合（MySQL 优化/K8s 运维）：剥离修饰词后归并到标准技能。"""

    def test_mysql_optimization_merged(self):
        # 剥离"优化"后归并到 MySQL，不分裂成独立技能节点
        result = JDExtractionResult(
            position_name="",
            requirements=[REQUIRESRelation(skill_name="MySQL 优化", necessity="must")],
        )
        out = post_process(result)
        assert out.requirements[0].skill_name == "MySQL"

    def test_k8s_ops_merged(self):
        result = JDExtractionResult(
            position_name="",
            skills=[SkillExtracted(name="K8s 运维")],
        )
        out = post_process(result)
        assert [s.name for s in out.skills] == ["Kubernetes"]

    def test_alias_key_not_stripped(self):
        # "性能优化" 命中别名归一为"性能调优"；后者 08-14 起停用（gold 口径
        # 不收上位泛词），归一后判噪过滤——别名归一的正确性由 normalize 单测保证
        result = JDExtractionResult(
            position_name="",
            skills=[SkillExtracted(name="性能优化")],
        )
        out = post_process(result)
        assert [s.name for s in out.skills] == []

    def test_whitelist_word_with_modifier_preserved(self):
        # 白名单词带修饰词（系统运维）整体保护，不剥离
        result = JDExtractionResult(
            position_name="",
            skills=[SkillExtracted(name="系统运维")],
        )
        out = post_process(result)
        assert [s.name for s in out.skills] == ["系统运维"]


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

    def test_single_char_fragment_filtered(self):
        """清洗后为单字且不在白名单 → 丢弃（防碎片入图）；白名单单字语言保留。"""
        result = JDExtractionResult(
            position_name="",
            skills=[
                SkillExtracted(name="X技术"),  # clean → "X"，非白名单单字 → 丢弃
                SkillExtracted(name="C技术"),  # clean → "C"，白名单单字语言 → 保留
            ],
        )
        out = post_process(result)
        assert [s.name for s in out.skills] == ["C"]

    def test_requirement_single_char_fragment_filtered(self):
        """requirements 同规则：清洗为单字碎片 → 该条剔除。"""
        result = JDExtractionResult(
            position_name="",
            requirements=[REQUIRESRelation(skill_name="X技术", necessity="must")],
        )
        out = post_process(result)
        assert out.requirements == []

    def test_soft_quality_noise_filtered_from_skills(self):
        """LLM 误抽的通用软素质词（吃苦耐劳/有责任心等）从技术技能剔除。

        SOFT_SKILL_NOISE：招聘软素质词不入技能图谱（区别于 SOFT_SKILL_WHITELIST
        的 20 项岗位本体软技能，后者仍经 soft_skills 保留）。
        """
        result = JDExtractionResult(
            position_name="",
            skills=[
                SkillExtracted(name="Python"),
                SkillExtracted(name="吃苦耐劳"),
                SkillExtracted(name="有责任心"),
                SkillExtracted(name="团队精神"),
                SkillExtracted(name="MySQL"),
            ],
        )
        out = post_process(result)
        assert [s.name for s in out.skills] == ["Python", "MySQL"]

    def test_soft_quality_noise_filtered_from_requirements(self):
        """requirements 同样剔除软素质词（软素质不作技能要求）。"""
        result = JDExtractionResult(
            position_name="",
            requirements=[
                REQUIRESRelation(skill_name="责任心强", necessity="must"),
                REQUIRESRelation(skill_name="Python", necessity="must"),
            ],
        )
        out = post_process(result)
        assert [(r.skill_name, r.necessity) for r in out.requirements] == [("Python", "must")]

    def test_tech_reliability_word_not_filtered(self):
        """可靠性工程技术词（可靠性测试/可靠性工程师）是技术技能，不应被软素质词表误杀。"""
        result = JDExtractionResult(
            position_name="",
            skills=[
                SkillExtracted(name="可靠性测试"),
                SkillExtracted(name="Python"),
            ],
        )
        out = post_process(result)
        assert [s.name for s in out.skills] == ["可靠性测试", "Python"]
