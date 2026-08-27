"""候选画像构建（方案 A：匹配主链路统一到 JD 级，岗位画像不再由图谱聚合）。

自 match.py 提取，供同步路由与 ARQ worker（match_recommend）共享。
岗位画像来源已改为 jd_raw 单条 JD（jd_match.py），本模块只保留候选人画像构建。
"""

from app.services.matching.schemas import (
    CandidateProfile,
    CandidateProject,
    CandidateSkill,
)


def build_candidate(parsed: dict) -> CandidateProfile:
    """从简历解析结果构建候选人画像。

    技能字段支持字符串列表或对象列表（resume_cache.parsed_data 两种形态兼容）。
    """
    skills: list[CandidateSkill] = []
    for s in parsed.get("skills", []):
        if isinstance(s, str):
            skills.append(CandidateSkill(skill_id=s, skill_name=s, proficiency=2))
        elif isinstance(s, dict):
            name = s.get("name", s.get("skill_id", ""))
            skills.append(CandidateSkill(
                skill_id=s.get("skill_id", name),
                skill_name=name,
                proficiency=int(s.get("proficiency", 2) or 2),
                low_confidence=bool(s.get("low_confidence", False)),
            ))

    projects: list[CandidateProject] = []
    for pr in parsed.get("projects", []):
        if isinstance(pr, str):
            projects.append(CandidateProject(name=pr))
        elif isinstance(pr, dict):
            projects.append(CandidateProject(
                name=pr.get("name", ""),
                stack=list(pr.get("stack", []) or []),
                description=pr.get("description", ""),
            ))

    certifications: list[str] = []
    for c in parsed.get("certifications", []):
        if isinstance(c, str):
            certifications.append(c)
        elif isinstance(c, dict) and c.get("name"):
            certifications.append(c["name"])

    return CandidateProfile(
        user_id=parsed.get("user_id", ""),
        skills=skills,
        total_years=float(parsed.get("total_years", 0) or 0),
        education_level=parsed.get("education_level"),
        domain_experience=parsed.get("domain_experience", []),
        projects=projects,
        certifications=certifications,
    )
