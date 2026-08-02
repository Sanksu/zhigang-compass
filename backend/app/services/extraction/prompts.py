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
6. 岗位-技能关系（requirements）：每个技能标注必要性 — "must"（必备）或 "nice"（加分）
7. 仅抽取文本中明确出现的内容，不要自行推断

JD 文本：
{jd_text}

输出 JSON："""

FEW_SHOT_EXAMPLES = """以下是几个示例：

示例 1：
JD 文本：招聘高级 Java 开发工程师，精通 Java、Spring Boot、MySQL，具备分布式系统经验，本科及以上学历
输出：{{"position_name": "Java 开发工程师", "level": "高级", "skills": [{{"name": "Java"}}, {{"name": "Spring Boot"}}, {{"name": "MySQL"}}, {{"name": "分布式系统"}}], "tools": [], "education": {{"level": "本科"}}, "requirements": [{{"skill_name": "Java", "necessity": "must"}}, {{"skill_name": "Spring Boot", "necessity": "must"}}, {{"skill_name": "MySQL", "necessity": "must"}}]}}

示例 2：
JD 文本：招聘 AI 产品经理，负责 AI 产品规划与设计，熟悉大模型应用者优先，硕士及以上学历，有 TOEFL 成绩优先
输出：{{"position_name": "AI 产品经理", "skills": [{{"name": "大模型应用"}}], "education": {{"level": "硕士"}}, "certifications": [{{"name": "TOEFL"}}], "requirements": [{{"skill_name": "大模型应用", "necessity": "nice"}}]}}
"""
