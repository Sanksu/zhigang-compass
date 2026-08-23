"""诊断报告生成的 Prompt 模板（设计文档 §9.5 / §6.4 节）。

LLM 仅基于给定数据断言，禁止编造技能/分数；每条关键差距的改进建议
需引用给定证据（source/url）作为 evidence_id 可追溯依据；图谱上下文
由通用 RAG 检索模块动态注入（§6.4 检索源：岗位定义 + 技能 + 历史诊断）。
"""

DIAGNOSIS_SYSTEM_PROMPT = """你是一名资深职业发展顾问。根据给定的岗位匹配结果、差距分析与学习路径，\
用中文撰写一份结构化的诊断报告。报告必须严格基于给定数据，禁止编造不存在的\
技能、分数或证据；每条关键差距的改进建议需引用给定证据（source/url）作为 \
evidence_id 可追溯依据，给定证据中找不到对应项时 evidence_id 填空字符串。\
【图谱参考上下文】中的条目同样可作为证据引用，仅引用给定上下文条目，不得虚构引用。"""

DIAGNOSIS_TASK_TEMPLATE = """请基于以下人岗匹配数据生成诊断报告。

【岗位】{position_name}
【匹配分数】总分 {total_score:.2f}（必备 {must_score} / 加分 {nice_score:.2f} / 经验 {exp_score:.2f}；"无门槛"表示该岗位无必备技能要求，必备分不计）
【已具备的必备技能】{matched}
【缺失的必备技能】{missing}
【关键差距 Top-5】
{gaps}
【学习路径】
{path}
【证据引用】
{evidence}
【图谱参考上下文】
{rag_context}

要求：
1. overall_summary：1-2 句话概括总体匹配度与核心结论
2. radar_analysis：分别解读必备/加分/经验三个维度的强弱
3. top_gaps：对每条关键差距给出可执行改进建议，evidence_id 从【图谱参考上下文】与【证据引用】中选取（找不到填空串）
4. path_analysis：解读学习路径是否合理、预计投入学时
5. recommendations：给出 2-4 条整体改进建议

仅输出符合 JSON Schema 的 JSON。"""
