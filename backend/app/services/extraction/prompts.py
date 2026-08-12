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
   禁止使用"技术"、"开发"、"工程师"等泛词（如"技术/后台"不入图）。
   关键：**必须保留领域限定词**——英文岗位名中的限定词须在中文名中体现，禁止丢弃。
   例如 "Data Scientist" → "数据科学家"（而非"科学家"）、"Applied Scientist" → "应用科学家"、
   "Machine Learning Engineer" → "机器学习工程师"（而非"工程师"）；多词限定无法精确翻译时
   保留核心限定词，宁细勿泛。
   英文 "Xxx Engineer / Xxx Scientist / Xxx Developer" 类岗位名：**必须完整翻译为
   "Xxx工程师/科学家/开发"**，禁止只取名词丢掉工种后缀——如 "BioChemical Engineer" →
   "生化工程师"（而非"生化"）、"Sr Systems Reliability Engineer" → "系统可靠性工程师"
   （而非"系统可靠性"）、"Verification Engineer" → "验证工程师"（而非"验证"）。
   输出必须是以"工程师/科学家/分析师/经理/设计师/架构师"等岗位词结尾的标准岗位名，
   不得输出"生化"、"固件"、"验证"这类无岗位语义的名词碎片。
   兜底规则（重要）：当文本首部出现 "岗位名称：xxx"（如 "岗位名称：Python爬虫工程师"）
   或首行即为明确标题（如智联 "文员（数据分析）" 首行）时，**必须直接取冒号后内容
   /首行标题作为岗位名**，并按上述规则翻译/规范化后输出，禁止留空——即使正文其余
   部分缺失或不完整，只要标题存在就不得返回空岗位名。
2. 技能（skills）：仅列出**必备**技术技能（如"Python"、"Java"、"数据分析"）。
   **"有 XX 经验者优先/熟悉 XX 更佳/了解 XX 加分"等加分项技能不得进入 skills**，
   只能进入 requirements 并标 "nice"（见规则 6）。禁止把行业、业务领域、招聘福利词
   列为技能（如保险/金融/银行/电商/医疗/教育/物流/车联网/五险一金/社保/公积金/
   双休/年终奖等）。
   **禁止把职责动词短语列为技能**："负责/参与/支持 XX 工作"中的动作短语
   （如"报表制作"、"报告生成"、"模型推理"、"数据清洗"）不是可独立匹配的技术技能，
   不得收录——仅名词化的独立技术点（如"数据建模"、"性能调优"）可收录。
   **必须细粒度拆分**：
   - 中文连接词（"与/及/和/或/、"）连接的两个不同技术概念**必须拆成独立技能**——
     "算法与数据结构" → "算法" + "数据结构"、"并行与分布式计算" → "并行计算" +
     "分布式计算"、"财务规划与分析" → "财务规划" + "财务分析"、"特征提取与匹配" →
     "特征提取" + "特征匹配"、"需求分析与方案" → "需求分析" + "方案设计"、
     "监控与可观测性" → "监控" + "可观测性"。连接词两侧是可独立学习/匹配的技术点，
     不得合并为一个笼统技能。
   - 斜杠 "/" 分隔的英文缩写/工具别名（"ETL/ELT"、"HTTP/HTTPS"、"REST/API"、
     "GitLab CI/CD"）是同一工具的两种写法，**不拆**。
   - 判定口诀：中文连接词连接的"两个不同名词" → 拆；斜杠分隔的"同一工具别名" → 不拆。
   宁可多列几个具体技能，不要用复合词笼统概括。
   **禁止把招聘软素质词列为技能**：吃苦耐劳/踏实肯干/敬业/诚信/细心/耐心/责任心/
   团队精神/团队合作/积极向上/勤奋/上进/职业素养 等是软素质表述，不是技术技能，
   不得出现在 skills 或 requirements 中（此类词无岗位区分度，会污染技能图谱与匹配）。
3. 工具（tools）：列出框架/工具，如"Spring Boot"、"Kubernetes"
4. 教育（education）：学历要求和专业要求
5. 证书（certifications）：需要的认证
6. 岗位-技能关系（requirements）：每个技能标注必要性 — "must"（必备）或 "nice"（加分），
   判定标准：JD 明确要求/硬性必备（如"精通/熟练掌握 XX"、任职要求清单中的技能）→ "must"；
   加分项/优先项（如"有 XX 经验者优先"、"熟悉 XX 更佳"、"了解 XX 加分"、辅助性技能）→ "nice"。
   把握不准时倾向 "nice"，避免全部标 "must" 失去区分度。
   **条件结构显式规则**：出现"一项或多项/任一/任选/或者/优先/更佳/加分"等条件标记时，
   该结构内的技能**一律标 "nice"**，且**不得出现在 skills 字段**（skills 仅收录必备技能）。
   示例："熟悉 Python、SQL 者优先" → skills 不含 Python/SQL，requirements 中 Python/SQL
   均标 "nice"。
   以及熟练度 level — "初级"/"中级"/"高级" 三档：文本含"了解/熟悉"→"初级"、
   "掌握/熟练"→"中级"、"精通/深入"→"高级"；JD 无明确熟练度表述时省略 level 字段
7. 薪资（salary_range）：提取文本中的薪资表述，保留原始写法，币种与单位
   （K/万/年/月/时/$/USD 等）必须保留，如 "20-40K"、"1-1.4万"、
   "USD 100000-150000/年"、"US$178K - US$241K (Employer provided)"、"面议"；
   仅薪资高低类表述计入，招聘福利（如"年终奖 14 薪"）不计；未提及薪资时省略该字段
8. 典型场景（typical_scenarios）：提取 JD 中明确描述的岗位核心工作场景/典型任务，
   每条为"名称 + 补充描述"：名称是 4-20 字的短短语（如"分布式缓存改造"、"信贷风控建模"、
   "车联网数据接入"），补充描述可含技术关键词与业务对象（如"Flink 实时计算，支撑业务报表"）。
   仅提取文本中明确出现的场景，禁止从技能名反向杜撰；JD 未描述具体场景时省略该字段
9. 仅抽取文本中明确出现的内容，不要自行推断："支持/涉及/面向 XX"等上下文性、
   平台性宽泛表述不得推断为技能；模型名/产品名（如 DeepSeek、Qwen、GPT）仅当作为
   明确技能要求出现时才收录，上下文提及不算技能。
   **宁缺毋滥**：宁可漏抽，不可多抽——误抽会污染技能图谱与岗位匹配。

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

示例 6（英文复合岗名完整翻译）：
JD 文本：BioChemical Engineer - Analog Devices, Wilmington MA. Design biochemical process solutions for semiconductor fabrication. Required: chemical engineering background, process design experience, Six Sigma certification. Salary: $120K-$150K/yr.
输出：{{"position_name": "生化工程师", "salary_range": "$120K-$150K/yr", "skills": [{{"name": "化学工程"}}, {{"name": "工艺设计"}}, {{"name": "Six Sigma"}}], "tools": [], "education": {{"level": "本科", "major": "化学工程"}}, "certifications": [{{"name": "Six Sigma"}}], "requirements": [{{"skill_name": "化学工程", "necessity": "must", "level": "中级"}}, {{"skill_name": "工艺设计", "necessity": "must"}}, {{"skill_name": "Six Sigma", "necessity": "nice"}}]}}

示例 7（英文复合岗名完整翻译，禁止丢 Engineer）：
JD 文本：Sr Systems Reliability Engineer - Responsible for system reliability engineering, automation of infrastructure, incident response. 5+ years experience, Linux, Python, Terraform required. Salary: USD 130000-160000/年.
输出：{{"position_name": "系统可靠性工程师", "salary_range": "USD 130000-160000/年", "skills": [{{"name": "Linux"}}, {{"name": "Python"}}, {{"name": "Terraform"}}, {{"name": "系统可靠性"}}], "tools": [{{"name": "Terraform"}}], "education": {{"level": "本科"}}, "requirements": [{{"skill_name": "Linux", "necessity": "must", "level": "高级"}}, {{"skill_name": "Python", "necessity": "must"}}, {{"skill_name": "Terraform", "necessity": "must"}}, {{"skill_name": "系统可靠性", "necessity": "must"}}]}}

示例 8（indeed 英文源，US$K 区间 + 雇主提供标注）：
JD 文本：Backend Software Engineer - Remote, US. Build distributed services with Go and PostgreSQL. 3+ years backend experience required. Salary: US$178K - US$241K (Employer provided).
输出：{{"position_name": "后端软件工程师", "salary_range": "US$178K - US$241K (Employer provided)", "skills": [{{"name": "Go"}}, {{"name": "PostgreSQL"}}, {{"name": "分布式系统"}}], "tools": [], "education": {{"level": "本科"}}, "requirements": [{{"skill_name": "Go", "necessity": "must", "level": "中级"}}, {{"skill_name": "PostgreSQL", "necessity": "must"}}, {{"skill_name": "分布式系统", "necessity": "nice"}}]}}

示例 9（典型场景抽取）：
JD 文本：招聘资深数据平台工程师，负责搭建实时数仓与离线数仓，参与 Flink 实时计算任务开发，支撑业务报表与推荐场景，要求熟悉大数据生态，本科及以上学历
输出：{{"position_name": "数据平台工程师", "level": "资深", "skills": [{{"name": "Flink"}}, {{"name": "数据仓库"}}, {{"name": "大数据"}}], "typical_scenarios": [{{"name": "实时数仓建设", "description": "Flink 实时计算，支撑业务报表"}}, {{"name": "离线数仓建模", "description": "分层建模，支撑推荐场景"}}], "tools": [], "education": {{"level": "本科"}}, "requirements": [{{"skill_name": "Flink", "necessity": "must", "level": "高级"}}, {{"skill_name": "数据仓库", "necessity": "must"}}]}}

示例 10（优先/加分条件正确示范——加分技能不进 skills）：
JD 文本：招聘数据分析师，精通 SQL 与 Python，熟悉 Tableau、Power BI 者优先，具备统计学基础，本科及以上学历，有电商行业经验更佳
输出：{{"position_name": "数据分析师", "skills": [{{"name": "SQL"}}, {{"name": "Python"}}, {{"name": "统计学"}}], "tools": [], "education": {{"level": "本科"}}, "requirements": [{{"skill_name": "SQL", "necessity": "must", "level": "高级"}}, {{"skill_name": "Python", "necessity": "must", "level": "高级"}}, {{"skill_name": "统计学", "necessity": "must"}}, {{"skill_name": "Tableau", "necessity": "nice"}}, {{"skill_name": "Power BI", "necessity": "nice"}}]}}
"""

BATCH_TASK_TEMPLATE = """从以下 {jd_count} 条 JD 文本中提取信息，输出 JSON 数组（每条 JD 对应一个对象，数组第 i 个元素对应"JD文本 i"）。

提取规则：
1. 岗位（position_name）：使用中文标准岗位名（如"前端开发工程师"），英文岗位名必须
   翻译为中文（如 "Software Engineer" → "软件工程师"）；不包含公司名/部门/团队名称，
   禁止使用"技术"、"开发"、"工程师"等泛词（如"技术/后台"不入图）。
   关键：**必须保留领域限定词**——英文岗位名中的限定词须在中文名中体现，禁止丢弃。
   例如 "Data Scientist" → "数据科学家"（而非"科学家"）、"Applied Scientist" → "应用科学家"、
   "Machine Learning Engineer" → "机器学习工程师"（而非"工程师"）；多词限定无法精确翻译时
   保留核心限定词，宁细勿泛。
   英文 "Xxx Engineer / Xxx Scientist / Xxx Developer" 类岗位名：**必须完整翻译为
   "Xxx工程师/科学家/开发"**，禁止只取名词丢掉工种后缀——如 "BioChemical Engineer" →
   "生化工程师"（而非"生化"）、"Sr Systems Reliability Engineer" → "系统可靠性工程师"
   （而非"系统可靠性"）、"Verification Engineer" → "验证工程师"（而非"验证"）。
   输出必须是以"工程师/科学家/分析师/经理/设计师/架构师"等岗位词结尾的标准岗位名，
   不得输出"生化"、"固件"、"验证"这类无岗位语义的名词碎片。
   兜底规则（重要）：当文本首部出现 "岗位名称：xxx"（如 "岗位名称：Python爬虫工程师"）
   或首行即为明确标题（如智联 "文员（数据分析）" 首行）时，**必须直接取冒号后内容
   /首行标题作为岗位名**，并按上述规则翻译/规范化后输出，禁止留空——即使正文其余
   部分缺失或不完整，只要标题存在就不得返回空岗位名。
2. 技能（skills）：仅列出**必备**技术技能（如"Python"、"Java"、"数据分析"）。
   **"有 XX 经验者优先/熟悉 XX 更佳/了解 XX 加分"等加分项技能不得进入 skills**，
   只能进入 requirements 并标 "nice"（见规则 6）。禁止把行业、业务领域、招聘福利词
   列为技能（如保险/金融/银行/电商/医疗/教育/物流/车联网/五险一金/社保/公积金/
   双休/年终奖等）。
   **禁止把职责动词短语列为技能**："负责/参与/支持 XX 工作"中的动作短语
   （如"报表制作"、"报告生成"、"模型推理"、"数据清洗"）不是可独立匹配的技术技能，
   不得收录——仅名词化的独立技术点（如"数据建模"、"性能调优"）可收录。
   **必须细粒度拆分**：
   - 中文连接词（"与/及/和/或/、"）连接的两个不同技术概念**必须拆成独立技能**——
     "算法与数据结构" → "算法" + "数据结构"、"并行与分布式计算" → "并行计算" +
     "分布式计算"、"财务规划与分析" → "财务规划" + "财务分析"、"特征提取与匹配" →
     "特征提取" + "特征匹配"、"需求分析与方案" → "需求分析" + "方案设计"、
     "监控与可观测性" → "监控" + "可观测性"。连接词两侧是可独立学习/匹配的技术点，
     不得合并为一个笼统技能。
   - 斜杠 "/" 分隔的英文缩写/工具别名（"ETL/ELT"、"HTTP/HTTPS"、"REST/API"、
     "GitLab CI/CD"）是同一工具的两种写法，**不拆**。
   - 判定口诀：中文连接词连接的"两个不同名词" → 拆；斜杠分隔的"同一工具别名" → 不拆。
   宁可多列几个具体技能，不要用复合词笼统概括。
   **禁止把招聘软素质词列为技能**：吃苦耐劳/踏实肯干/敬业/诚信/细心/耐心/责任心/
   团队精神/团队合作/积极向上/勤奋/上进/职业素养 等是软素质表述，不是技术技能，
   不得出现在 skills 或 requirements 中（此类词无岗位区分度，会污染技能图谱与匹配）。
3. 工具（tools）：列出框架/工具，如"Spring Boot"、"Kubernetes"
4. 教育（education）：学历要求和专业要求
5. 证书（certifications）：需要的认证
6. 岗位-技能关系（requirements）：每个技能标注必要性 — "must"（必备）或 "nice"（加分），
   判定标准：JD 明确要求/硬性必备（如"精通/熟练掌握 XX"、任职要求清单中的技能）→ "must"；
   加分项/优先项（如"有 XX 经验者优先"、"熟悉 XX 更佳"、"了解 XX 加分"、辅助性技能）→ "nice"。
   把握不准时倾向 "nice"，避免全部标 "must" 失去区分度。
   **条件结构显式规则**：出现"一项或多项/任一/任选/或者/优先/更佳/加分"等条件标记时，
   该结构内的技能**一律标 "nice"**，且**不得出现在 skills 字段**（skills 仅收录必备技能）。
   示例："熟悉 Python、SQL 者优先" → skills 不含 Python/SQL，requirements 中 Python/SQL
   均标 "nice"。
   以及熟练度 level — "初级"/"中级"/"高级" 三档：文本含"了解/熟悉"→"初级"、
   "掌握/熟练"→"中级"、"精通/深入"→"高级"；JD 无明确熟练度表述时省略 level 字段
7. 薪资（salary_range）：提取文本中的薪资表述，保留原始写法，币种与单位
   （K/万/年/月/时/$/USD 等）必须保留，如 "20-40K"、"1-1.4万"、
   "USD 100000-150000/年"、"US$178K - US$241K (Employer provided)"、"面议"；
   仅薪资高低类表述计入，招聘福利（如"年终奖 14 薪"）不计；未提及薪资时省略该字段
8. 典型场景（typical_scenarios）：提取 JD 中明确描述的岗位核心工作场景/典型任务，
   每条为"名称 + 补充描述"：名称是 4-20 字的短短语（如"分布式缓存改造"、"信贷风控建模"、
   "车联网数据接入"），补充描述可含技术关键词与业务对象（如"Flink 实时计算，支撑业务报表"）。
   仅提取文本中明确出现的场景，禁止从技能名反向杜撰；JD 未描述具体场景时省略该字段
9. 仅抽取文本中明确出现的内容，不要自行推断："支持/涉及/面向 XX"等上下文性、
   平台性宽泛表述不得推断为技能；模型名/产品名（如 DeepSeek、Qwen、GPT）仅当作为
   明确技能要求出现时才收录，上下文提及不算技能。
   **宁缺毋滥**：宁可漏抽，不可多抽——误抽会污染技能图谱与岗位匹配。

JD 文本：
{jd_texts}

输出 JSON 数组："""
