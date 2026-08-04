"""词典归一化单元测试（设计文档 §5.2 / 岗位名归一化）。

覆盖技能别名归一化、岗位名归一化（同义合并/技术栈细分/英文翻译/泛词不入图）。
核心模块覆盖率目标 ≥ 80%（设计文档 §15.3）。
"""

import pytest

from app.services.extraction.dictionary import (
    SKILL_WHITELIST,
    SOFT_SKILL_WHITELIST,
    _normalize_base,
    _translate_en_position,
    normalize_position_name,
    normalize_proficiency,
    normalize_skill,
)


class TestSoftSkillWhitelist:
    """软技能白名单约束（设计文档 9.2：岗位本体维护共 20 项）。"""

    def test_exactly_20_items(self):
        assert len(SOFT_SKILL_WHITELIST) == 20

    def test_is_subset_of_skill_whitelist(self):
        # 软技能是技能白名单的标记性子集，JD/简历抽取共用同一枚举域
        assert SOFT_SKILL_WHITELIST.issubset(SKILL_WHITELIST)

    def test_representative_entries(self):
        assert {"团队协作", "沟通能力", "项目管理", "领导力"}.issubset(SOFT_SKILL_WHITELIST)


class TestNormalizeSkill:
    def test_alias_case_insensitive(self):
        assert normalize_skill("golang") == "Go"
        assert normalize_skill("Golang") == "Go"
        assert normalize_skill("spring") == "Spring Boot"
        assert normalize_skill("vue") == "Vue.js"
        assert normalize_skill("k8s") == "Kubernetes"

    def test_no_alias_returns_raw(self):
        assert normalize_skill("Python") == "Python"
        assert normalize_skill("  Redis  ") == "Redis"  # 首尾空白去除

    def test_chinese_alias(self):
        assert normalize_skill("大模型") == "大语言模型"
        assert normalize_skill("自然语言处理算法") == "自然语言处理"


class TestNormalizePositionName:
    def test_synonym_merge(self):
        assert normalize_position_name("前端开发") == "前端开发工程师"
        assert normalize_position_name("web前端开发工程师") == "前端开发工程师"
        assert normalize_position_name("高级前端开发工程师") == "前端开发工程师"

    def test_tech_stack_subdivision(self):
        assert normalize_position_name("React前端开发工程师") == "React前端开发工程师"
        assert normalize_position_name("前端开发工程师(React)") == "React前端开发工程师"
        assert normalize_position_name("Vue前端开发工程师") == "Vue前端开发工程师"

    def test_mixed_title(self):
        # 混合标题整体关键词匹配，避免产生"后"这类碎片岗位
        assert normalize_position_name("前端开发/后端") == "前端开发工程师"

    def test_en_translation_merged_with_chinese(self):
        # 英文翻译结果再过中文归一化，与中文路径统一（软件工程师→软件开发工程师）
        assert normalize_position_name("Software Engineer") == "软件开发工程师"
        assert normalize_position_name("Senior Software Engineer") == "软件开发工程师"
        # 机器学习工程师 → 翻译后归入算法族（_POSITION_KEYWORDS 含"机器学习"）
        assert normalize_position_name("machine learning engineer") == "算法工程师"
        assert normalize_position_name("frontend developer") == "前端开发工程师"

    def test_intl_source_en_translation(self):
        # 国际源冷门英文岗位 → 翻译归并到中文族（_EN_POSITION_MAP 扩充条目）
        assert normalize_position_name("Business Analytics Senior Analyst") == "数据分析师"
        assert normalize_position_name("Sr. Analyst, Marketing Analytics") == "数据分析师"
        assert normalize_position_name("Model/Anlys/Valid Sr Analyst") == "数据分析师"
        assert normalize_position_name("Data Automation Engineer") == "大数据开发工程师"
        assert normalize_position_name("Snowflake Engineer") == "大数据开发工程师"
        assert normalize_position_name("Kafka Streaming Architect") == "大数据开发工程师"
        assert normalize_position_name("Inference Engineer, GPU Kernel Optimization") == "算法工程师"
        # 安全岗翻译后含"网络"关键词，与现有 security engineer 一致归网络族
        assert normalize_position_name("Threat Context Analyst") == "网络工程师"
        assert normalize_position_name(
            "Advanced Cyber Threat Response & Forensics Lead/Manager"
        ) == "网络工程师"
        assert normalize_position_name("Member of Technical Staff") == "软件开发工程师"
        assert normalize_position_name("Seismic Developer") == "软件开发工程师"
        assert normalize_position_name("Engineering Manager, Platform Engineering") == "运维工程师"
        assert normalize_position_name("Senior Supervisor, Quality Engineering") == "测试工程师"
        assert normalize_position_name("Sensor Test R&D Mechatronics Engineer") == "嵌入式开发工程师"
        assert normalize_position_name("RFIC System Engineer") == "硬件工程师"

    def test_unmappable_en_kept_unchanged(self):
        # 金融/专业独有岗位无法归类，归一化返回原名（脚本跳过，保留英文）
        assert normalize_position_name("Manager, Logistics") == "Manager, Logistics"
        assert normalize_position_name(
            "Executive Director - North America Delta 1 Flow Swaps Trading"
        ) == "Executive Director - North America Delta 1 Flow Swaps Trading"

    def test_generic_words_are_empty(self):
        # 泛词不入图：归一化结果为空串由 kg_service 跳过
        assert normalize_position_name("技术") == ""
        assert normalize_position_name("开发") == ""
        assert normalize_position_name("工程师") == ""

    def test_backend_family(self):
        assert normalize_position_name("后台") == "后端开发工程师"
        assert normalize_position_name("服务端开发") == "后端开发工程师"


class TestTranslateEnPosition:
    def test_exact(self):
        assert _translate_en_position("data scientist") == "数据分析师"

    def test_comma_head(self):
        assert _translate_en_position("backend engineer, core platform") == "后端开发工程师"

    def test_substring(self):
        # 长标题含已知英文岗位名时按最长子串匹配
        assert _translate_en_position("Senior Full Stack Engineer II") == "全栈工程师"

    def test_unknown_returns_none(self):
        assert _translate_en_position("chief happiness officer") is None


class TestNormalizeBase:
    def test_suffix_loop(self):
        # 循环去后缀收敛到关键词族
        assert _normalize_base("Python后端开发工程师") == "后端开发工程师"

    def test_keyword_family(self):
        assert _normalize_base("数仓工程师") == "大数据开发工程师"
        assert _normalize_base("嵌入式工程师") == "嵌入式开发工程师"


class TestNormalizeProficiency:
    """熟练度三档映射（了解/熟悉→初级、掌握→中级、精通→高级）。"""

    def test_normalized_enum_passthrough(self):
        assert normalize_proficiency("初级") == "初级"
        assert normalize_proficiency("中级") == "中级"
        assert normalize_proficiency("高级") == "高级"

    def test_high_level_keywords(self):
        assert normalize_proficiency("精通") == "高级"
        assert normalize_proficiency("深入理解") == "高级"
        assert normalize_proficiency("资深") == "高级"

    def test_mid_level_keywords(self):
        assert normalize_proficiency("掌握") == "中级"
        assert normalize_proficiency("熟练使用") == "中级"
        assert normalize_proficiency("熟练掌握") == "中级"  # 高级词优先不误判

    def test_low_level_keywords(self):
        assert normalize_proficiency("熟悉") == "初级"
        assert normalize_proficiency("了解") == "初级"
        assert normalize_proficiency("入门") == "初级"

    def test_unknown_returns_none(self):
        assert normalize_proficiency("") is None
        assert normalize_proficiency("加分项") is None
        assert normalize_proficiency(None) is None
