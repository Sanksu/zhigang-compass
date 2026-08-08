"""JD 实体抽取的 Prompt 模板。

分层设计（System + Task + Few-Shot + Input），见设计文档 6.2 节。
"""

SYSTEM_PROMPT = """你是一个专业的招聘信息分析助手。你的任务是从招聘 JD 文本中\
提取结构化信息，包括岗位名称、技能要求、工具、教育要求、证书要求等。\
请严格遵循 JSON Schema 输出。"""

TASK_TEMPLATE = """从以下 JD 文本中提取信息，以 JSON 格式输出。

提取规则：
1. 岗位（position_name）：使用中文标准岗位名（如"前端开发工程师"），英文岗位名必须
   翻译为中文（如 "Software Engineer" → "软件工程师"）；不包含公司名/部门/团队名称，
   禁止使用"技术"、"开发"、"工程师"等泛词（如"技术/后台"不入图）
2. 技能（skills）：仅列出技术技能（如"Python"、"Java"、"数据分析"）。禁止把行业、
   业务领域、招聘福利词列为技能（如保险/金融/银行/电商/医疗/教育/物流/车联网/
   五险一金/社保/公积金/双休/年终奖等）
3. 工具（tools）：列出框架/工具，如"Spring Boot"、"Kubernetes"
4. 教育（education）：学历要求和专业要求
5. 证书（certifications）：需要的认证
6. 岗位-技能关系（requirements）：每个技能标注必要性 — "must"（必备）或 "nice"（加分），
   判定标准：JD 明确要求/硬性必备（如"精通/熟练掌握 XX"、任职要求清单中的技能）→ "must"；
   加分项/优先项（如"有 XX 经验者优先"、"熟悉 XX 更佳"、"了解 XX 加分"、辅助性技能）→ "nice"。
   把握不准时倾向 "nice"，避免全部标 "must" 失去区分度。
   以及熟练度 level — "初级"/"中级"/"高级" 三档：文本含"了解/熟悉"→"初级"、
   "掌握/熟练"→"中级"、"精通/深入"→"高级"；JD 无明确熟练度表述时省略 level 字段
7. 仅抽取文本中明确出现的内容，不要自行推断

JD 文本：
{jd_text}

输出 JSON："""

FEW_SHOT_EXAMPLES = """以下是几个示例：

示例 1：
JD 文本：招聘高级 Java 开发工程师，精通 Java、Spring Boot、MySQL，具备分布式系统经验，本科及以上学历
输出：{{"position_name": "Java 开发工程师", "level": "高级", "skills": [{{"name": "Java"}}, {{"name": "Spring Boot"}}, {{"name": "MySQL"}}, {{"name": "分布式系统"}}], "tools": [], "education": {{"level": "本科"}}, "requirements": [{{"skill_name": "Java", "necessity": "must", "level": "高级"}}, {{"skill_name": "Spring Boot", "necessity": "must"}}, {{"skill_name": "MySQL", "necessity": "must"}}]}}

示例 2：
JD 文本：招聘 AI 产品经理，负责 AI 产品规划与设计，熟悉大模型应用者优先，硕士及以上学历，有 TOEFL 成绩优先
输出：{{"position_name": "AI 产品经理", "skills": [{{"name": "大模型应用"}}], "education": {{"level": "硕士"}}, "certifications": [{{"name": "TOEFL"}}], "requirements": [{{"skill_name": "大模型应用", "necessity": "nice", "level": "初级"}}]}}

示例 3：
JD 文本：招聘资深数据仓库工程师，精通 SQL 与 Hive，熟练使用 Spark、Airflow 调度任务，熟悉数据建模方法论，计算机相关专业本科及以上学历，持有 AWS 数据类认证者优先
输出：{{"position_name": "数据仓库工程师", "level": "资深", "skills": [{{"name": "SQL"}}, {{"name": "Hive"}}, {{"name": "Spark"}}, {{"name": "Airflow"}}, {{"name": "数据建模"}}], "tools": [], "education": {{"level": "本科", "major": "计算机"}}, "certifications": [{{"name": "AWS 数据类认证"}}], "requirements": [{{"skill_name": "SQL", "necessity": "must", "level": "高级"}}, {{"skill_name": "Hive", "necessity": "must", "level": "高级"}}, {{"skill_name": "Spark", "necessity": "must", "level": "中级"}}, {{"skill_name": "Airflow", "necessity": "must"}}, {{"skill_name": "数据建模", "necessity": "must"}}]}}

示例 4：
JD 文本：招聘前端开发工程师，精通 React 与 TypeScript，掌握前端工程化，熟练使用 ECharts 做数据可视化，大专以上学历，具备良好的团队协作与沟通能力
输出：{{"position_name": "前端开发工程师", "skills": [{{"name": "React"}}, {{"name": "TypeScript"}}, {{"name": "前端工程化"}}, {{"name": "ECharts"}}, {{"name": "数据可视化"}}], "tools": [], "education": {{"level": "大专"}}, "soft_skills": ["团队协作", "沟通能力"], "requirements": [{{"skill_name": "React", "necessity": "must", "level": "高级"}}, {{"skill_name": "TypeScript", "necessity": "must", "level": "高级"}}, {{"skill_name": "ECharts", "necessity": "nice"}}]}}

示例 5：
JD 文本：招聘网络安全工程师，负责渗透测试与安全运维，熟悉 Linux 与 Python，1-3 年经验，本科及以上学历，持有 CISP 或 OSCP 证书者优先
输出：{{"position_name": "网络安全工程师", "skills": [{{"name": "Linux"}}, {{"name": "Python"}}, {{"name": "渗透测试"}}], "tools": [], "education": {{"level": "本科"}}, "certifications": [{{"name": "CISP"}}, {{"name": "OSCP"}}], "requirements": [{{"skill_name": "Linux", "necessity": "must", "level": "中级"}}, {{"skill_name": "Python", "necessity": "must", "level": "中级"}}, {{"skill_name": "渗透测试", "necessity": "must"}}]}}
"""

BATCH_TASK_TEMPLATE = """从以下 {jd_count} 条 JD 文本中提取信息，输出 JSON 数组（每条 JD 对应一个对象，数组第 i 个元素对应"JD文本 i"）。

提取规则：
1. 岗位（position_name）：使用中文标准岗位名（如"前端开发工程师"），英文岗位名必须
   翻译为中文（如 "Software Engineer" → "软件工程师"）；不包含公司名/部门/团队名称，
   禁止使用"技术"、"开发"、"工程师"等泛词（如"技术/后台"不入图）
2. 技能（skills）：仅列出技术技能（如"Python"、"Java"、"数据分析"）。禁止把行业、
   业务领域、招聘福利词列为技能（如保险/金融/银行/电商/医疗/教育/物流/车联网/
   五险一金/社保/公积金/双休/年终奖等）
3. 工具（tools）：列出框架/工具，如"Spring Boot"、"Kubernetes"
4. 教育（education）：学历要求和专业要求
5. 证书（certifications）：需要的认证
6. 岗位-技能关系（requirements）：每个技能标注必要性 — "must"（必备）或 "nice"（加分），
   判定标准：JD 明确要求/硬性必备（如"精通/熟练掌握 XX"、任职要求清单中的技能）→ "must"；
   加分项/优先项（如"有 XX 经验者优先"、"熟悉 XX 更佳"、"了解 XX 加分"、辅助性技能）→ "nice"。
   把握不准时倾向 "nice"，避免全部标 "must" 失去区分度。
   以及熟练度 level — "初级"/"中级"/"高级" 三档：文本含"了解/熟悉"→"初级"、
   "掌握/熟练"→"中级"、"精通/深入"→"高级"；JD 无明确熟练度表述时省略 level 字段
7. 仅抽取文本中明确出现的内容，不要自行推断

JD 文本：
{jd_texts}

输出 JSON 数组："""
