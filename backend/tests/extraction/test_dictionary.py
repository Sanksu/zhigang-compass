"""词典归一化单元测试（设计文档 §5.2 / 岗位名归一化）。

覆盖技能别名归一化、岗位名归一化（同义合并/技术栈细分/英文翻译/泛词不入图）。
核心模块覆盖率目标 ≥ 80%（设计文档 §15.3）。
"""


from app.services.extraction.dictionary import (
    SOFT_SKILL_WHITELIST,
    _POSITION_PREFIX_RE,
    _clean_variant,
    _normalize_base,
    _translate_en_position,
    _variant_key,
    normalize_position_name,
    normalize_proficiency,
    normalize_skill,
)


class TestSoftSkillWhitelist:
    """软技能白名单约束（设计文档 9.2：岗位本体维护共 20 项）。"""

    def test_exactly_20_items(self):
        assert len(SOFT_SKILL_WHITELIST) == 20

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
        # 失真兜底族（算法工程师，2026-08-09 追加治理）：无方向词的纯算法岗不再
        # 作为聚合目的地——无技能不入图；带通用算法技能（机器学习/深度学习等）仍归本族
        assert normalize_position_name("算法工程师") == ""
        assert normalize_position_name("资深算法工程师", skills=["机器学习"]) == "算法工程师"
        assert normalize_position_name("机器学习工程师", skills=["深度学习"]) == "算法工程师"
        # 带方向技能则路由到细分族，不再混入通用算法族
        assert normalize_position_name("算法工程师", skills=["大语言模型", "PyTorch"]) == "大模型算法工程师"

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

    def test_whitelist_backfill_20260814(self):
        # 08-14 白名单补录（用户确认）：鸿蒙变体合并 / STEM讲师合并 / 统计师入图 /
        # IC验证规范后缀；复合词限定防误吸
        assert normalize_position_name("鸿蒙前端开发工程师") == "鸿蒙开发工程师"
        assert normalize_position_name("鸿蒙全栈工程师") == "鸿蒙开发工程师"
        assert normalize_position_name("鸿蒙应用开发工程师") == "鸿蒙开发工程师"
        assert normalize_position_name("鸿蒙生态运营") == ""  # 非开发岗不误吸
        assert normalize_position_name("课后STEM讲师") == "STEM讲师"
        assert normalize_position_name("stem工程师") != "STEM讲师"  # 复合词防误吸
        assert normalize_position_name("统计师") == "统计师"  # 修复不入图
        assert normalize_position_name("生物统计师") == "生物统计师"  # 不被统计师族吸收
        assert normalize_position_name("IC验证") == "IC验证工程师"
        assert normalize_position_name("IC验证工程师") == "IC验证工程师"

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
        # 英文翻译结果再过中文归一化，与中文路径统一；软件开发工程师为失真兜底族
        # （2026-08-09 治理）：无技能不入图，提供技能时按技能路由到细分族
        assert normalize_position_name("Software Engineer") == ""
        assert normalize_position_name("Software Engineer", skills=["Python", "Django"]) == "后端开发工程师"
        assert normalize_position_name("Senior Software Engineer", skills=["React"]) == "前端开发工程师"
        # 机器学习工程师 → 翻译后归入算法族（失真兜底族，2026-08-09 追加治理）：
        # 无技能不入图，带通用算法技能仍归算法工程师
        assert normalize_position_name("machine learning engineer") == ""
        assert normalize_position_name("machine learning engineer", skills=["PyTorch"]) == "算法工程师"
        assert normalize_position_name("frontend developer") == "前端开发工程师"

    def test_intl_source_en_translation(self):
        # 国际源冷门英文岗位 → 翻译归并到中文族（_EN_POSITION_MAP 扩充条目）
        assert normalize_position_name("Business Analytics Senior Analyst") == "数据分析师"
        assert normalize_position_name("Sr. Analyst, Marketing Analytics") == "数据分析师"
        assert normalize_position_name("Model/Anlys/Valid Sr Analyst") == "数据分析师"
        assert normalize_position_name("Data Automation Engineer") == "大数据开发工程师"
        assert normalize_position_name("Snowflake Engineer") == "大数据开发工程师"
        assert normalize_position_name("Kafka Streaming Architect") == "大数据开发工程师"
        # 失真兜底族（算法工程师，2026-08-09 追加治理）：无技能不入图，带技能按路由归位
        assert normalize_position_name("Inference Engineer, GPU Kernel Optimization") == ""
        assert normalize_position_name(
            "Inference Engineer, GPU Kernel Optimization", skills=["CUDA", "机器学习"]
        ) == "算法工程师"
        # 安全岗翻译后命中 P1 新增安全族（不再并入网络族）
        assert normalize_position_name("Threat Context Analyst") == "网络安全工程师"
        assert normalize_position_name(
            "Advanced Cyber Threat Response & Forensics Lead/Manager"
        ) == "网络安全工程师"
        # 失真兜底族（软件开发/硬件）不再作为聚合目的地：无技能不入图
        assert normalize_position_name("Member of Technical Staff") == ""
        assert normalize_position_name("Seismic Developer") == ""
        assert normalize_position_name("Engineering Manager, Platform Engineering") == "运维工程师"
        assert normalize_position_name("Senior Supervisor, Quality Engineering") == "测试工程师"
        assert normalize_position_name("Sensor Test R&D Mechatronics Engineer") == "嵌入式开发工程师"
        assert normalize_position_name("RFIC System Engineer") == ""

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
        # 无细分核心词的通用"分析师"兜底：2026-08-08 P0 归一化评估后改为停用词
        # 拦截（国际源 Analyst 泛化，无细分信息量，不入图）
        assert normalize_position_name("分析师") == ""
        # 细分仅对"分析师"结尾生效：非分析师岗位不被误吸。
        # "精算师"为金融岗（Actuary），2026-08-08 P0 停用词拦截，不入图
        assert normalize_position_name("精算师") == ""
        # 失真兜底族（研究员）不再作为聚合目的地：无技能不入图
        assert normalize_position_name("量化研究员") == ""
        # "商业智能"为业务词碎片（P2 清理）：剥后缀后命中停用词 → 空
        assert normalize_position_name("商业智能工程师") == ""
        # 失真兜底族（科学家）不再作为聚合目的地：无技能不入图
        assert normalize_position_name("研究科学家") == ""
        # 已有细分族优先命中，不受兜底族影响
        assert normalize_position_name("数据分析师") == "数据分析师"

    def test_generic_family_routed_by_skills(self):
        # 失真兜底族移除聚合（2026-08-09 治理）：命中兜底词时按 JD 技能路由到细分族，
        # 无技能/技能未命中 → 不入图
        assert normalize_position_name("软件开发工程师", skills=["Python", "Django"]) == "后端开发工程师"
        assert normalize_position_name("软件开发工程师", skills=["React", "TypeScript"]) == "前端开发工程师"
        assert normalize_position_name("软件开发工程师", skills=["Spark", "Hadoop"]) == "大数据开发工程师"
        assert normalize_position_name("软件开发工程师", skills=["PyTorch", "机器学习"]) == "算法工程师"
        assert normalize_position_name("软件工程师", skills=["Java"]) == "Java开发工程师"
        assert normalize_position_name("研究科学家", skills=["机器学习"]) == "算法工程师"
        assert normalize_position_name("架构师", skills=["Kubernetes"]) == "DevOps工程师"
        assert normalize_position_name("硬件工程师", skills=["FPGA"]) == "嵌入式开发工程师"
        # 2026-08-09 增强：统计/计量技能优先归数据分析师，不被通用算法族抢走
        assert normalize_position_name("算法工程师", skills=["因果推断", "Python"]) == "数据分析师"
        assert normalize_position_name("算法工程师", skills=["统计学", "回归建模", "SAS"]) == "数据分析师"
        assert normalize_position_name("算法工程师", skills=["双重差分", "BigQuery"]) == "数据分析师"
        # 2026-08-09 增强：视频/动作识别方向归机器视觉
        assert normalize_position_name("算法工程师", skills=["动作识别", "多目标跟踪", "视频处理"]) == "机器视觉算法工程师"
        # 技能未命中路由词（无技术方向）→ 不入图
        assert normalize_position_name("软件开发工程师", skills=["沟通能力"]) == ""
        # 无技能 → 不入图
        assert normalize_position_name("软件开发工程师") == ""
        assert normalize_position_name("科学家") == ""
        assert normalize_position_name("专家") == ""
        assert normalize_position_name("顾问") == ""
        # 细分族优先于兜底词，不受影响
        assert normalize_position_name("嵌入式软件工程师") == "嵌入式开发工程师"
        assert normalize_position_name("数据科学家") == "数据科学家"
        assert normalize_position_name("UI工程师") == "前端开发工程师"

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

    def test_whitelist_residual_positions_kept(self):
        # 白名单改造（2026-08-12）：审计确认的合法低频岗位登记为精确族后保留入图
        assert normalize_position_name("IT系统管理员") == "IT系统管理员"
        assert normalize_position_name("产品助理") == "产品助理"
        assert normalize_position_name("技术教师") == "技术教师"
        assert normalize_position_name("投诉处理助理") == "投诉处理助理"
        assert normalize_position_name("计算生物学家") == "计算生物学家"
        assert normalize_position_name("首席统计师") == "首席统计师"

    def test_whitelist_blocks_new_chinese_fragments(self):
        # 白名单改造：纯中文剥壳残留核心词不在白名单 → 不入图（防新碎片）
        # 区别于既有停用词：这些是未登记停用词的未知碎片，靠白名单兜底拦截
        assert normalize_position_name("智能") == ""
        assert normalize_position_name("人事") == ""
        assert normalize_position_name("激光工艺") == ""

    def test_whitelist_keeps_mixed_cn_en_residual(self):
        # 白名单改造：含非中文的残留（公司名+岗位词、技术缩写）维持原语义保留，
        # 不因白名单收紧被误拦（"Google工程师"剥壳残留"Google"为设计允许）
        assert normalize_position_name("LLM应用") == "LLM应用"
        assert normalize_position_name("Google工程师") != ""

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
        # 2026-08-08 P0 归一化评估新增：泛词/非技术岗/招聘形态低质岗
        assert normalize_position_name("分析师") == ""
        assert normalize_position_name("程序员") == ""
        assert normalize_position_name("行政管理") == ""
        assert normalize_position_name("人力资源") == ""
        assert normalize_position_name("业务分析") == ""
        assert normalize_position_name("货代销售") == ""
        assert normalize_position_name("商业水电维修工") == ""
        assert normalize_position_name("一级建造师") == ""
        assert normalize_position_name("研究助理") == ""
        assert normalize_position_name("项目助理") == ""
        # 派生形态（前缀修饰剥除后命中停用词）
        assert normalize_position_name("行政管理主管") == ""
        assert normalize_position_name("人力资源经理") == ""
        assert normalize_position_name("综合行政高级专员") == ""
        assert normalize_position_name("材料实验室行政专员") == ""
        assert normalize_position_name("人事行政助理") == ""
        assert normalize_position_name("副总裁，量化策略师") == ""
        # 2026-08-08 P0 方案 2：非技术/金融/工具泛岗低频单例清理防复发
        assert normalize_position_name("交易员") == ""
        assert normalize_position_name("经济学家") == ""
        assert normalize_position_name("流行病学家") == ""
        assert normalize_position_name("精算师") == ""
        assert normalize_position_name("量化策略师") == ""
        assert normalize_position_name("Guidewire") == ""
        assert normalize_position_name("可视化软件开发工程师") == ""
        assert normalize_position_name("桌面") == ""

    def test_p0_low_quality_position_governance(self):
        # 2026-08-09 P0 图谱低质岗治理（develop）：英文复合岗名归位标准族，碎片词拦截
        # 归位（完整岗位名）
        assert normalize_position_name("BioChemical Engineer") == "生化工程师"
        assert normalize_position_name("Bio-Optics Engineer") == "生物光学工程师"
        assert normalize_position_name("Assistant Estimator") == "成本估算师"
        assert normalize_position_name("Verification Engineer III") == "测试工程师"
        assert normalize_position_name("Quality Engineer") == "测试工程师"
        assert normalize_position_name("Senior Firmware Engineer") == "嵌入式开发工程师"
        assert normalize_position_name("Privacy Engineer") == "网络安全工程师"
        # 失真兜底族治理（be-position-governance）：Solution/Physical Design 映射到
        # 兜底族（解决方案工程师/硬件工程师）后按技能路由，无技能不入图
        assert normalize_position_name("Solution Engineer") == ""
        assert normalize_position_name("Physical Design Engineer") == ""

    def test_p0b_en_position_map_expansion(self):
        # P0-B 英文岗位映射扩充（2026-08-12）：审计发现 526 个纯英文技术岗
        # 未映射（654 条记录）不入图。补齐常见技术岗映射到标准族。
        # 明确技术岗 → 归位标准族
        assert normalize_position_name("Java Developer") == "Java开发工程师"
        assert normalize_position_name("Senior Java Developer") == "Java开发工程师"
        assert normalize_position_name("Python Developer") == "Python开发工程师"
        assert normalize_position_name("Staff Python Engineer") == "Python开发工程师"
        assert normalize_position_name("Test Automation Engineer") == "测试工程师"
        assert normalize_position_name("UI Developer") == "前端开发工程师"
        assert normalize_position_name("Front End Engineer") == "前端开发工程师"
        assert normalize_position_name("Business Intelligence Engineer") == "大数据开发工程师"
        assert normalize_position_name("Server Engineer") == "运维工程师"
        assert normalize_position_name("Cloud Engineer") == "运维工程师"
        assert normalize_position_name("Robotics Engineer") == "机器人算法工程师"
        # Software Development Engineer → 失真兜底族：无技能不入图，带技能路由细分族
        assert normalize_position_name("Software Development Engineer") == ""
        assert normalize_position_name(
            "Software Development Engineer", skills=["Java", "Spring"]
        ) == "后端开发工程师"  # Spring 框架族优先于 Java 语言族（设计意图）
        # Algorithm Engineer → 失真兜底族：无技能不入图，带通用算法技能归算法族
        assert normalize_position_name("Algorithm Engineer") == ""
        assert normalize_position_name(
            "Algorithm Engineer", skills=["Python", "PyTorch"]
        ) == "算法工程师"
        # 最长子串匹配：长标题内嵌已知岗位名可命中（逗号后主标题）
        assert normalize_position_name("Knowledge Engineer - Back End") == ""
        assert normalize_position_name(
            "Knowledge Engineer - Back End", skills=["Python", "NLP"]
        ) == "大模型算法工程师"  # NLP 命中大模型路由族
        # 停用词冲突已解：AI/ML Applied Engineer 从停用词表移除，走映射
        assert normalize_position_name(
            "AI/ML Applied Engineer", skills=["Python"]
        ) == "Python开发工程师"
        # 无映射的英文复合头衔仍拦截（不入图）
        assert normalize_position_name("SCADA Migration & Integration Lead") == ""
        # 无归属碎片拦截
        assert normalize_position_name("验证") == ""
        assert normalize_position_name("质量") == ""
        assert normalize_position_name("隐私") == ""
        assert normalize_position_name("性能工程") == ""
        assert normalize_position_name("信息化") == ""
        assert normalize_position_name("劳动力分析总监") == ""
        # 海外源验证（be-position-governance 2026-08-09）：Application Developer 抽成
        # "应用"碎片拦截；AI 研究归位"研究员"兜底族无技能不入图；ICAM 归位网安
        assert normalize_position_name("应用") == ""
        assert normalize_position_name("AI 研究") == ""
        assert normalize_position_name("ICAM") == "网络安全工程师"
        assert normalize_position_name("ICAM工程师") == "网络安全工程师"

    def test_p0a_legit_positions_not_filtered(self):
        # P0-A 停用词只拦碎片：真实细分岗不受影响
        assert normalize_position_name("保险分析师") == "保险分析师"
        assert normalize_position_name("投资分析师") == "投资分析师"
        assert normalize_position_name("策略分析师") == "策略分析师"
        assert normalize_position_name("可持续发展分析师") == "可持续发展分析师"
        # 英文未翻译岗 P2 一并停用（低频脏边，见清理决策）
        # P10（08-16）例外：AI Infra Engineer 翻译丢后缀碎片 → AI基础设施工程师族
        assert normalize_position_name("AI Infra Engineer") == "AI基础设施工程师"
        assert normalize_position_name("Manager, Logistics") == ""
        assert normalize_position_name("量化分析师") == "量化分析师"  # 细分族优先，不因"量化"停用词被拦
        # 2026-08-08 P0 停用词扩充后，细分分析师岗不受"分析师"兜底拦截影响
        assert normalize_position_name("市场分析师") == "市场分析师"
        assert normalize_position_name("商业智能分析师") == "商业智能分析师"
        assert normalize_position_name("业务分析师") == "业务分析师"
        assert normalize_position_name("信贷分析师") == "信贷分析师"
        assert normalize_position_name("财务分析师") == "财务分析师"
        # 精算分析师为 _ANALYST_SUB_FAMILIES 细分岗：不受"精算师"停用词影响
        assert normalize_position_name("精算分析师") == "精算分析师"

    def test_p0_cjk_guard_and_short_keyword(self):
        # P0-2 CJK 守卫 + P0-3/AI 短关键词修复：混合标题不再被英文子串劫持
        assert normalize_position_name("网络 SRE 工程师") == "DevOps工程师"  # sre 归 DevOps 族
        # 顾问为失真兜底族：无技能不入图（不再被 "ai" 子串误归算法）
        assert normalize_position_name("SailPoint 顾问") == ""
        # 英文自动化测试岗：P0 方案 1 增加 test engineer 映射后归位测试工程师
        assert normalize_position_name("Web & Mobile Automation Test Engineer") == "测试工程师"

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


class TestAIGenericRouting:
    """AI 泛词族按技能路由（T-04 第二批，2026-08-15）。

    AI 前缀/拼凑岗位名（无空格变体不命中带空格关键词族）加入
    _GENERIC_ROUTED_FAMILIES 后按 JD 技能路由到细分族；业务技能
    不在路由表 → 返回空串不入图（与失真兜底族口径一致）。
    """

    def test_ai_generic_routes_to_sub_family(self):
        assert normalize_position_name("AI应用", ["Python", "机器学习"]) == "算法工程师"
        assert normalize_position_name("AI应用", ["Python", "计算机视觉"]) == "机器视觉算法工程师"
        assert normalize_position_name("AI产品", ["大模型", "RAG"]) == "大模型算法工程师"
        assert normalize_position_name("AI智能体", ["大模型", "Agent"]) == "大模型算法工程师"
        assert normalize_position_name("AIoT", ["嵌入式", "C++"]) == "嵌入式开发工程师"
        # P10（08-16）AI基础设施 已建独立族（AI Infra Engineer 翻译丢后缀），
        # 无空格变体同族——不再走泛词路由（T-04 泛词条目已随 P10 清理）
        assert normalize_position_name("AI基础设施", ["Kubernetes", "Docker"]) == "AI基础设施工程师"

    def test_ai_generic_no_skills_returns_empty(self):
        # 无技能/未命中路由 → 不入图（宁缺毋滥）
        assert normalize_position_name("AI应用", None) == ""
        assert normalize_position_name("应用AI客户", ["客户成功", "CRM"]) == ""
        assert normalize_position_name("零售运营与AI参与", ["零售运营"]) == ""
        assert normalize_position_name("云AI客户", ["销售"]) == ""

    def test_ai_generic_spaced_variant_still_routes(self):
        # 带空格变体（"AI 应用"）走算法族关键词 → 路由不回归
        assert normalize_position_name("AI 应用", ["Python", "机器学习"]) == "算法工程师"


class TestP10FragmentFallback:
    """P10 英文裸词/中文碎片兜底（2026-08-16 legacy 决策）。

    LLM 把 "Senior Web Engineer" 压成裸词 "Web"、把 "AI Infra Engineer"
    翻译成丢后缀的 "AI 基础设施"——兜底映射到规范岗位名。
    """

    def test_web_bare_word_mapped(self):
        assert normalize_position_name("Web") == "Web开发工程师"

    def test_ai_fragment_mapped(self):
        assert normalize_position_name("AI 基础设施") == "AI基础设施工程师"
        assert normalize_position_name("AI 生产力") == "AI生产力工程师"
        assert normalize_position_name("AI与数据风险管理") == "AI与数据风险管理经理"

    def test_web_no_false_positive(self):
        # 词边界保护：不误伤 WebGL/WebSphere/web前端
        assert normalize_position_name("WebGL开发工程师") == "WebGL开发工程师"
        assert normalize_position_name("WebSphere管理员") == "WebSphere管理员"
        assert normalize_position_name("web前端开发工程师") == "前端开发工程师"


class TestPositionVariantCleaning:
    """重复岗位对治理：字符级变体键/输出收敛/语义别名（2026-08-16）。"""

    def test_variant_key_whitespace_and_fullwidth(self):
        assert _variant_key("CMDB 发现") == _variant_key("CMDB发现") == "cmdb发现"
        assert _variant_key("ＡＩ数据科学") == _variant_key("AI数据科学") == "ai数据科学"

    def test_variant_key_keeps_ascii_punct(self):
        # C++/C# 依赖 ASCII 标点，不得与 C/C# 合并
        assert _variant_key("C++开发工程师") == "c++开发工程师"
        assert _variant_key("C开发工程师") != _variant_key("C++开发工程师")

    def test_variant_key_strips_cjk_punct(self):
        assert _variant_key("产品、运营经理") == _variant_key("产品运营经理")

    def test_clean_variant_removes_whitespace_only(self):
        assert _clean_variant("AI 数据科学机器人教练") == "AI数据科学机器人教练"
        assert _clean_variant("ＡＩ") == "AI"
        assert _clean_variant("C++ 开发工程师") == "C++开发工程师"  # ASCII 标点保留

    def test_normalize_converges_whitespace_variant(self):
        assert normalize_position_name("AI 数据科学机器人教练") == "AI数据科学机器人教练"
        assert normalize_position_name("React 前端开发工程师") == "React前端开发工程师"

    def test_normalize_existing_paths_unaffected(self):
        # 关键词/翻译/停用词路径零回归
        assert normalize_position_name("前端开发工程师") == "前端开发工程师"
        assert normalize_position_name("web") == "Web开发工程师"
        assert normalize_position_name("Software Engineer") == ""
        assert normalize_position_name("实习") == ""

    def test_alias_redirect(self, monkeypatch):
        # 语义别名：键/值为岗位名原文，入口按变体键收敛（临时注入）
        from app.services.extraction import dictionary as d

        monkeypatch.setitem(d._POSITION_ALIAS, "AI数据科学与机器人教练", "AI数据科学机器人教练")
        monkeypatch.setattr(
            d, "_POSITION_ALIAS_BY_VARIANT",
            {d._variant_key(k): v for k, v in d._POSITION_ALIAS.items()},
        )
        assert normalize_position_name("AI 数据科学与机器人教练") == "AI数据科学机器人教练"
        assert normalize_position_name("AI数据科学与机器人教练") == "AI数据科学机器人教练"
        # 无关岗位名与英文翻译路径不受别名影响
        assert normalize_position_name("前端开发工程师") == "前端开发工程师"
        assert normalize_position_name("Software Engineer") == ""

    def test_alias_table_consistency(self):
        # CI 把关：键值非空、键值不同、变体键唯一、无自映射（空表幂等通过）。
        # 跨组别名合法（AS400应用 → AS400应用程序 变体键不同，属归一目标迁移），
        # 不强制 vk(键) == vk(值)；值须为清洗后规范名（无空白，防输出分裂）
        from app.services.extraction import dictionary as d

        seen: set[str] = set()
        for k, v in d._POSITION_ALIAS.items():
            assert k and v
            assert k != v
            vk = d._variant_key(k)
            assert vk not in seen  # 一个变体键只能对应一个规范名
            seen.add(vk)
            assert d._clean_variant(v) == v  # 值须为清洗后形式（无空白）


class TestP11FragmentRedirect:
    """P11 岗位名碎片归位（2026-08-16 岗位处置）：团队名/技术栈名/产品名
    按 JD 技能语义归位到规范岗位；词边界防误伤。"""

    def test_en_fragments(self):
        assert normalize_position_name("Staff", ["Java", "Spring"]) == "后端开发工程师"
        assert normalize_position_name("UX") == "UX设计师"
        assert normalize_position_name("UX设计师") == "UX设计师"  # 幂等

    def test_cn_fragments(self):
        assert normalize_position_name("FPGA团队") == "FPGA验证"
        assert normalize_position_name("Kubernetes与OpenShift") == "DevOps工程师"
        assert normalize_position_name("Endur技术") == "后端开发工程师"
        assert normalize_position_name("STEM课程") == "STEM科技教育讲师"
        assert normalize_position_name("仪器AIT") == "仪器AIT工程师"
        assert normalize_position_name("OBD标定") == "OBD标定工程师"
        assert normalize_position_name("TAK") == "移动开发工程师"
        assert normalize_position_name("CFD分析") == "CFD分析工程师"

    def test_no_false_positive(self):
        # 词边界保护：stack 含 tak 不误伤；IT 泛词不拦（T-04 决策）；web 族先行
        assert normalize_position_name("stack") == "stack"
        assert normalize_position_name("IT系统管理员") == "IT系统管理员"
        assert normalize_position_name("web前端开发工程师") == "前端开发工程师"


class TestP11LateReview:
    """Stage B 晚复核新对（2026-08-16）：AI基础设施支持归位 + AI客户类拦截。"""

    def test_ai_infra_support_aliased(self):
        assert normalize_position_name("AI基础设施支持") == "AI基础设施工程师"
        assert normalize_position_name("AI 基础设施支持") == "AI基础设施工程师"

    def test_ai_infra_no_space_keyword(self):
        # 清洗后无空格输入命中 P10 无空格变体（#260 清洗暴露的兼容缺陷：
        # 原关键词带空格，无空格输入剥壳后落入 AI 泛词族被路由为空）
        assert normalize_position_name("AI基础设施") == "AI基础设施工程师"
        assert normalize_position_name("AI 基础设施") == "AI基础设施工程师"
        assert normalize_position_name("AI生产力") == "AI生产力工程师"
        assert normalize_position_name("AI基础设施工程师") == "AI基础设施工程师"

    def test_ai_customer_blocked(self):
        assert normalize_position_name("AI客户") == ""
        assert normalize_position_name("应用AI客户") == ""
