"""词典归一化单元测试（设计文档 §5.2 / 岗位名归一化）。

覆盖技能别名归一化、岗位名归一化（同义合并/技术栈细分/英文翻译/泛词不入图）。
核心模块覆盖率目标 ≥ 80%（设计文档 §15.3）。
"""

import pytest

from app.services.extraction.dictionary import (
    SKILL_WHITELIST,
    SOFT_SKILL_WHITELIST,
    _POSITION_PREFIX_RE,
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


class TestSkillWhitelist:
    """技能白名单应覆盖高频真实技能（历史审计：SQL/AWS/Azure 等绕过白名单）。"""

    def test_high_freq_real_skills_covered(self):
        # 图谱审计中白名单外的高频真实技能，必须入白名单
        assert {"SQL", "AWS", "Azure", "GCP", "Linux", "Tableau", "Agile",
                "Excel", "NoSQL", "SAS", "ETL", "Snowflake", "DevOps",
                "Power BI", "RESTful API", "JSON", "API", "JIRA",
                "Maven", "JUnit", "Hibernate", "Pandas", "Transformer",
                "AI", "C", "MATLAB"}.issubset(SKILL_WHITELIST)


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

    def test_whitelist_case_normalized(self):
        # 白名单词的大小写变体统一到标准写法（历史数据存在 GO/Matlab/Javascript 等变体）
        assert normalize_skill("GO") == "Go"
        assert normalize_skill("go") == "Go"
        assert normalize_skill("MATLAB") == "MATLAB"
        assert normalize_skill("matlab") == "MATLAB"
        assert normalize_skill("Echarts") == "ECharts"
        assert normalize_skill("Javascript") == "JavaScript"
        assert normalize_skill("FASTAPI") == "FastAPI"
        assert normalize_skill("LANGCHAIN") == "LangChain"
        assert normalize_skill("Hbase") == "HBase"

    def test_chinese_alias(self):
        assert normalize_skill("大模型") == "大语言模型"
        assert normalize_skill("自然语言处理算法") == "自然语言处理"


class TestP1AliasExpansion:
    """P1-1：高频同义变体归一化到白名单标准词（评估报告 3.4 别名缺失）。"""

    def test_frontend_aliases(self):
        assert normalize_skill("Vue3") == "Vue.js"
        assert normalize_skill("vue2") == "Vue.js"
        assert normalize_skill("ReactJS") == "React"
        assert normalize_skill("ES6") == "JavaScript"
        assert normalize_skill("Element Plus") == "ElementUI"
        assert normalize_skill("uniapp") == "uni-app"
        assert normalize_skill("微信小程序") == "小程序"

    def test_backend_aliases(self):
        assert normalize_skill("SpringMVC") == "Spring MVC"
        assert normalize_skill("MyBatis-Plus") == "MyBatis"
        assert normalize_skill("REST API") == "RESTful API"
        assert normalize_skill("restful") == "RESTful API"
        assert normalize_skill("MQ") == "消息队列"
        assert normalize_skill("SQL优化") == "SQL"
        assert normalize_skill("微服务架构") == "微服务"

    def test_ai_aliases(self):
        assert normalize_skill("NLP") == "自然语言处理"
        assert normalize_skill("Prompt Engineering") == "提示工程"
        assert normalize_skill("prompt工程") == "提示工程"
        assert normalize_skill("AI Agent") == "Agentic AI"
        assert normalize_skill("SFT") == "模型微调"

    def test_other_aliases(self):
        assert normalize_skill("沟通协作") == "沟通能力"
        assert normalize_skill("敏捷") == "敏捷开发"
        assert normalize_skill("大屏可视化") == "数据可视化"
        assert normalize_skill("ROS2") == "ROS"


class TestP2AliasExpansion:
    """P2-B 同义异构归一（岗位评估报告 4.1：AI 编码工具/Agent 生态表述碎片统一
    到白名单标准词，防同一技能建出多个图谱节点）。"""

    def test_ai_coding_tool_aliases(self):
        assert normalize_skill("ai coding") == "AI辅助编程"
        assert normalize_skill("AI-assisted coding") == "AI辅助编程"
        assert normalize_skill("AI编程") == "AI辅助编程"
        assert normalize_skill("AI辅助编码") == "AI辅助编程"
        assert normalize_skill("AI 辅助编程") == "AI辅助编程"
        assert normalize_skill("copilot") == "GitHub Copilot"
        assert normalize_skill("GitHub Copilot") == "GitHub Copilot"
        assert normalize_skill("claude code") == "Claude Code"
        assert normalize_skill("cursor") == "Cursor"
        assert normalize_skill("codex") == "Codex"
        assert normalize_skill("chatgpt") == "ChatGPT"
        assert normalize_skill("genai") == "GenAI"

    def test_tech_stack_aliases(self):
        assert normalize_skill("jvm") == "JVM"
        assert normalize_skill(".net core") == ".NET"
        assert normalize_skill("nodejs") == "Node.js"
        assert normalize_skill("postgres") == "PostgreSQL"
        assert normalize_skill("milvus") == "Milvus"
        assert normalize_skill("dbt") == "dbt"
        assert normalize_skill("databricks") == "Databricks"


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

    def test_algorithm_subdivision(self):
        # 评估报告 P1-A：算法岗按方向细分（细分族前置），纯"算法工程师"回退通用族
        assert normalize_position_name("大模型算法工程师") == "大模型算法工程师"
        assert normalize_position_name("大模型算法") == "大模型算法工程师"
        assert normalize_position_name("多模态大模型算法工程师") == "大模型算法工程师"
        assert normalize_position_name("AI Agent/大模型应用工程师") == "大模型算法工程师"
        assert normalize_position_name("自动驾驶算法工程师") == "自动驾驶算法工程师"
        assert normalize_position_name("泊车算法工程师") == "自动驾驶算法工程师"
        assert normalize_position_name("VLA算法工程师") == "自动驾驶算法工程师"
        assert normalize_position_name("车辆控制算法工程师") == "自动驾驶算法工程师"
        assert normalize_position_name("飞控算法工程师") == "自动驾驶算法工程师"
        assert normalize_position_name("XR相机与机器视觉工程师") == "机器视觉算法工程师"
        assert normalize_position_name("增长算法工程师") == "推荐搜索算法工程师"
        assert normalize_position_name("机器学习搜索工程经理") == "推荐搜索算法工程师"
        # 无方向词的纯算法岗仍归通用算法族
        assert normalize_position_name("算法工程师") == "算法工程师"
        assert normalize_position_name("资深算法工程师") == "算法工程师"
        assert normalize_position_name("机器学习工程师") == "算法工程师"

    def test_algorithm_subdivision_no_false_positive(self):
        # 细分关键词为"方向词+算法"复合词：裸方向词不误吸非算法岗/停用词
        assert normalize_position_name("LLM应用") == "LLM应用"
        assert normalize_position_name("多模态理解") == ""
        assert normalize_position_name("自动驾驶系统") == ""
        # "智能体平台"是平台名误抽岗位（P2 清理），不入图
        assert normalize_position_name("智能体平台") == ""

    def test_short_keyword_no_false_positive(self):
        # 问题 1：短关键词子串误吸（go/ui/搜索 误吸非目标岗）
        assert normalize_position_name("google工程师") != "Go开发工程师"
        assert normalize_position_name("UI设计师") == "UI设计师"
        assert normalize_position_name("UI工程师") == "前端开发工程师"  # 非设计师 UI 岗仍归前端
        assert normalize_position_name("搜索运营") == ""
        assert normalize_position_name("搜索引擎优化") == ""
        # 正向：go/ui 关键词对目标岗仍命中
        assert normalize_position_name("Go开发工程师") == "Go开发工程师"
        assert normalize_position_name("golang开发") == "Go开发工程师"

    def test_untranslated_pure_en_filtered(self):
        # 问题 2：未翻译的纯英文岗位名直接拦截（不入图），不再靠停用词逐条点杀
        assert normalize_position_name("VP of Engineering") == ""
        assert normalize_position_name("Chief Operating Officer") == ""
        assert normalize_position_name("Engineering Manager") == ""
        # 含中文的未识别名不受拦截影响（仍走中文规则路径）
        assert normalize_position_name("Google工程师") != ""

    def test_tech_stack_subdivision_not_blocked_by_stopword(self):
        # 问题 4：技术栈细分岗位 base 剥到泛词（开发/工程师）时不应整体丢弃
        assert normalize_position_name("鸿蒙开发工程师") == "鸿蒙开发工程师"
        assert normalize_position_name("桌面开发工程师") == "桌面开发工程师"

    def test_prefix_regex_deduplicated(self):
        # 问题 5：前缀正则中"资深"重复出现（同一候选去重）
        assert _POSITION_PREFIX_RE.pattern.count("资深") == 1


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
        # 安全岗翻译后命中 P1 新增安全族（不再并入网络族）
        assert normalize_position_name("Threat Context Analyst") == "网络安全工程师"
        assert normalize_position_name(
            "Advanced Cyber Threat Response & Forensics Lead/Manager"
        ) == "网络安全工程师"
        assert normalize_position_name("Member of Technical Staff") == "软件开发工程师"
        assert normalize_position_name("Seismic Developer") == "软件开发工程师"
        assert normalize_position_name("Engineering Manager, Platform Engineering") == "运维工程师"
        assert normalize_position_name("Senior Supervisor, Quality Engineering") == "测试工程师"
        assert normalize_position_name("Sensor Test R&D Mechatronics Engineer") == "嵌入式开发工程师"
        assert normalize_position_name("RFIC System Engineer") == "硬件工程师"

    def test_unmappable_en_filtered(self):
        # P2 清理：无法映射的英文未翻译岗低频脏边，一并停用不入图
        # （此前保留待 P0-B 映射扩充；用户决策改为直接清理）
        assert normalize_position_name("Manager, Logistics") == ""
        assert normalize_position_name(
            "Executive Director - North America Delta 1 Flow Swaps Trading"
        ) == ""

    def test_generic_words_are_empty(self):
        # 泛词不入图：归一化结果为空串由 kg_service 跳过
        assert normalize_position_name("技术") == ""
        assert normalize_position_name("开发") == ""
        assert normalize_position_name("工程师") == ""

    def test_internship_filtered(self):
        # 实习类岗位不入图（招聘形态，非正式岗位族）
        assert normalize_position_name("财务分析师实习生") == ""
        assert normalize_position_name("对日开发实习生") == ""
        assert normalize_position_name("研究实习员") == ""
        assert normalize_position_name("实习前端开发") == ""

    def test_analyst_scientist_suffix_family(self):
        # 分析师已拆细分族（方案 C）：财务/业务/量化 等核心词命中细分标准名
        assert normalize_position_name("财务分析师") == "财务分析师"
        assert normalize_position_name("业务分析师") == "业务分析师"
        assert normalize_position_name("量化分析师") == "量化分析师"
        assert normalize_position_name("信贷政策与决策分析师") == "信贷分析师"
        assert normalize_position_name("数据建模分析师") == "数据分析师"
        # 无细分核心词的通用"分析师"兜底保留（原统一族行为）
        assert normalize_position_name("分析师") == "分析师"
        # 细分仅对"分析师"结尾生效：非分析师岗位不被误吸
        assert normalize_position_name("精算师") == "精算师"
        assert normalize_position_name("量化研究员") == "研究员"
        # "商业智能"为业务词碎片（P2 清理）：剥后缀后命中停用词 → 空
        assert normalize_position_name("商业智能工程师") == ""
        # 科学家族未拆，保持统一族
        assert normalize_position_name("研究科学家") == "科学家"
        # 已有细分族优先命中，不受兜底族影响
        assert normalize_position_name("数据分析师") == "数据分析师"

    def test_trailing_level_word_stripped(self):
        # 尾部级别词剥离；"DevOps 高级"命中 P1 新增 DevOps 族 → DevOps工程师
        assert normalize_position_name("DevOps 高级") == "DevOps工程师"
        # 剥离后残留词"包裹洞察与定价"为业务碎片（P2 清理）→ 空
        assert normalize_position_name("包裹洞察与定价高级") == ""

    def test_p0_fragment_words_filtered(self):
        # P0-1 碎片词二次过滤：剥后缀残留的无信息量核心词不入图
        assert normalize_position_name("产品") == ""
        assert normalize_position_name("项目") == ""
        assert normalize_position_name("数据") == ""
        assert normalize_position_name("经理") == ""
        assert normalize_position_name("董事总经理") == ""
        assert normalize_position_name("研究") == ""
        assert normalize_position_name("知识") == ""
        assert normalize_position_name("系统") == ""

    def test_p0a_noise_positions_filtered(self):
        # 岗位评估报告 P0-A：LLM 误抽业务词/碎片岗位名（低频空岗）不入图
        assert normalize_position_name("专利") == ""
        assert normalize_position_name("传播") == ""
        assert normalize_position_name("跟单员") == ""
        assert normalize_position_name("量化") == ""
        assert normalize_position_name("中训练") == ""
        assert normalize_position_name("后训练") == ""
        assert normalize_position_name("前向部署") == ""
        assert normalize_position_name("大客户销售") == ""
        assert normalize_position_name("定制服装导购") == ""
        assert normalize_position_name("短视频编导") == ""
        assert normalize_position_name("项目申报销售") == ""
        assert normalize_position_name("电子发现协调员") == ""
        assert normalize_position_name("设施合同与投标") == ""
        assert normalize_position_name("MEC运营") == ""
        assert normalize_position_name("OSINT 情报收集员") == ""
        assert normalize_position_name("Palantir 前向部署") == ""
        assert normalize_position_name("产品交付") == ""
        assert normalize_position_name("信贷支持") == ""
        assert normalize_position_name("数据生产") == ""
        assert normalize_position_name("零售运营分析") == ""
        assert normalize_position_name("多模态理解") == ""
        assert normalize_position_name("应用研究") == ""
        assert normalize_position_name("廉政审计") == ""
        assert normalize_position_name("报表分析") == ""
        assert normalize_position_name("桥梁设计") == ""
        assert normalize_position_name("特效工具") == ""
        assert normalize_position_name("自动驾驶系统") == ""

    def test_p0a_legit_positions_not_filtered(self):
        # P0-A 停用词只拦碎片：真实细分岗不受影响
        assert normalize_position_name("保险分析师") == "保险分析师"
        assert normalize_position_name("投资分析师") == "投资分析师"
        assert normalize_position_name("策略分析师") == "策略分析师"
        assert normalize_position_name("可持续发展分析师") == "可持续发展分析师"
        # 英文未翻译岗 P2 一并停用（低频脏边，见清理决策）
        assert normalize_position_name("AI Infra Engineer") == ""
        assert normalize_position_name("Manager, Logistics") == ""
        assert normalize_position_name("量化分析师") == "量化分析师"  # 细分族优先，不因"量化"停用词被拦

    def test_p0_cjk_guard_and_short_keyword(self):
        # P0-2 CJK 守卫 + P0-3/AI 短关键词修复：混合标题不再被英文子串劫持
        assert normalize_position_name("网络 SRE 工程师") == "DevOps工程师"  # sre 归 DevOps 族
        assert normalize_position_name("SailPoint 顾问") == "顾问"  # 不再被 "ai" 子串误归算法
        # 英文自动化测试岗：P2 清理未翻译英文岗 → 空（此前保留，测试断言已更新）
        assert normalize_position_name("Web & Mobile Automation Test Engineer") == ""

    def test_p1_new_families(self):
        # P1 新增族：产品/项目/创始/安全/DevOps/数据科学家/数据库
        assert normalize_position_name("产品经理") == "产品经理"
        assert normalize_position_name("项目经理") == "项目经理"
        assert normalize_position_name("Founding Engineer") == "创始工程师"
        assert normalize_position_name("网络安全工程师") == "网络安全工程师"
        assert normalize_position_name("Security Engineer") == "网络安全工程师"
        assert normalize_position_name("DevOps 高级工程师") == "DevOps工程师"
        assert normalize_position_name("站点可靠性工程师") == "DevOps工程师"
        assert normalize_position_name("数据科学家") == "数据科学家"
        assert normalize_position_name("研究数据科学家") == "数据科学家"
        assert normalize_position_name("数据库管理员") == "数据库管理员"
        assert normalize_position_name("PostgreSQL 负责人") == "数据库管理员"
        # 配套：数据工程族（P0-1 stopwords 扩充后防真实岗位丢失）
        assert normalize_position_name("数据工程师") == "大数据开发工程师"
        assert normalize_position_name("数据工程负责人") == "大数据开发工程师"

    def test_p1_skill_word_not_position(self):
        # P1 技能词不入图：归一化结果命中技能白名单 → 空串
        assert normalize_position_name("SQL") == ""
        assert normalize_position_name("PyTorch") == ""
        assert normalize_position_name("Agent") == ""
        assert normalize_position_name("OpenShift") == ""
        assert normalize_position_name("C") == ""

    def test_backend_family(self):
        assert normalize_position_name("后台") == "后端开发工程师"
        assert normalize_position_name("服务端开发") == "后端开发工程师"


class TestTranslateEnPosition:
    def test_exact(self):
        assert _translate_en_position("data scientist") == "数据科学家"

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
