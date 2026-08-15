"""课程技能标签抽取（T-05，2026-08-15）。

背景：icourse163/edx 爬虫不产出 skills 字段（edx 写死空、icourse163 页面
无数据）→ 课程无 LEARNABLE_VIA 静态边（存量 974 门孤立课程，产品走
learning_path 语义兜底无功能缺陷）。本模块对新采集课程做 LLM 技能抽取，
门控后写回 snapshot["skills"]，load_courses 阶段随之建静态边。

防脏边（08-13 #192/#198 教训）：抽取结果经 canonical_skill_name +
_is_valid_skill_name（停用词/白名单口径，与 import_course 一致）过滤，
未通过即丢弃；LLM 不可用/解析失败静默降级（不阻塞 ETL，与 RAG 接地同语义）。
"""

from pydantic import BaseModel, Field

from app.services.extraction.post_processor import (
    _is_valid_skill_name,
    canonical_skill_name,
)

# 课程技能抽取数量约束（防 LLM 输出整页概念清单）
MAX_SKILLS = 15

SYSTEM_PROMPT = """你是一个课程分析助手。你的任务是从课程标题与描述中提取
该课程教授的核心技能，供技能图谱课程推荐使用。"""

TASK_TEMPLATE = """从以下课程信息中提取技能标签，以 JSON 格式输出。

课程标题：{title}
课程描述：{description}

要求：
1. 只提取该课程实际教授的、可学习的技能（编程语言/框架/工具/领域知识/方法），
   不要输出课程平台、机构、授课形式等信息
2. 使用标准技能名（如 "Python"、"机器学习"、"Docker"），不要加"学习/课程/
   入门"等修饰词
3. 输出 {max_skills} 个以内；信息不足时宁少勿滥，可以输出空数组
4. 输出 JSON：{{"skills": ["技能1", "技能2", ...]}}
"""


class CourseSkillResult(BaseModel):
    """课程技能抽取结果（Pydantic 强校验，LLM 输出非法自动重试）。"""

    skills: list[str] = Field(default_factory=list, description="课程教授的核心技能")


def build_prompt(title: str, description: str) -> str:
    """组装课程技能抽取 prompt。"""
    return TASK_TEMPLATE.format(
        title=title.strip() or "（无标题）",
        description=(description or "").strip()[:500] or "（无描述）",
        max_skills=MAX_SKILLS,
    )


def extract_course_skills(llm, title: str, description: str) -> list[str]:
    """LLM 抽取课程技能并过门控（canonical 归一化 + 停用词/白名单过滤）。

    Args:
        llm: LLMProviderChain 实例（None 或不可用时返回空）
        title/description: 课程标题/描述

    Returns:
        通过门控的技能名列表（已 canonical_skill_name 归一化，去重保序）
    """
    if llm is None:
        return []
    try:
        result = llm.extract_structured(
            build_prompt(title, description),
            response_model=CourseSkillResult,
            system_prompt=SYSTEM_PROMPT,
            timeout=30,
        )
    except Exception:
        # LLM 不可用/超时/校验失败：静默降级（课程无标签不影响语义兜底推荐）
        return []
    skills: list[str] = []
    seen: set[str] = set()
    for raw in result.skills or []:
        name = canonical_skill_name((raw or "").strip())
        if not name or not _is_valid_skill_name(name) or name in seen:
            continue
        seen.add(name)
        skills.append(name)
    return skills


def filter_skill_tags(skills: list) -> list[str]:
    """存量标签规范化（爬虫原始标签 → canonical + 门控，供写回前统一口径）。

    与 extract_course_skills 的门控一致；处理爬虫产出（coursera 段落解析）
    与 LLM 产出两种来源。
    """
    out: list[str] = []
    seen: set[str] = set()
    for s in skills:
        if isinstance(s, dict):
            s = s.get("name") or s.get("skill") or ""
        raw = str(s or "").strip()
        name = canonical_skill_name(raw)
        if not name or not _is_valid_skill_name(name) or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out
