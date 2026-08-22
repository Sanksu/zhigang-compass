"""匹配领域：候选人画像构建 + 图谱岗位画像加载。

自 match.py 提取，供同步路由与 ARQ worker（match_recommend）共享，
避免 worker 反向依赖 API 路由模块。
"""

import time

from app.core.database import neo4j_driver
from app.services.matching.schemas import (
    CandidateProfile,
    CandidateProject,
    CandidateSkill,
    Necessity,
    PositionProfile,
    SkillRequirement,
)
from app.services.matching.semantic import SkillEmbedder

# 岗位画像进程级缓存：图谱结构变化低频，TTL 内复用，
# 避免每次匹配请求实时聚合（97 岗位 × ~15000 边）拖慢响应
_POSITIONS_CACHE_TTL = 300  # 秒
_positions_cache: dict = {"ts": 0.0, "positions": None}

# 软技能要求在独立通道中的展示权重（差距列表排序用；不参与评分——
# 2026-08-22 拍板软技能退出 must/nice 评分池，仅保留差距展示）
_SOFT_SKILL_WEIGHT = 0.4

# 软技能类目值（与 configs/skill_whitelist.yaml 中 category 命名一致，仅展示打标）
SOFT_SKILL_CATEGORY = "软技能"


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


def _load_positions_uncached() -> list[PositionProfile]:
    """从图谱聚合岗位画像（无进程缓存；加载时批量预热技能向量）。

    语义向量缓存到 SkillEmbedder，评分阶段全部 cache hit，
    避免首次匹配请求触发全量 embedding 计算（>30s 前端超时的主因）。
    """
    positions: dict[str, PositionProfile] = {}
    with neo4j_driver.session() as session:
        rows = session.run(
            """
            MATCH (p:Position)-[r:REQUIRES]->(s:Skill)
            // 边缘岗位过滤（08-20）：剔除单/低频岗位（freq<3，多为 1 条 JD 支撑的
            // 噪声岗位，如 GSBOA/Clay/TeamCenter基础设施管理员）与 legacy 状态岗位，
            // 避免"文本相关但证据薄弱"的边缘岗位混入匹配推荐
            WHERE p.freq IS NOT NULL AND p.freq >= 3
              AND coalesce(p.status, '') <> 'legacy'
            RETURN p.id AS pid,
                   p.name AS pname,
                   p.required_years AS req_years, p.last_updated AS last_updated,
                   p.industry AS industry,
                   s.id AS sid, s.name AS sname, s.category AS category,
                   r.necessity AS necessity, r.weight AS weight,
                   r.level AS level, r.source_count AS source_count
            """
        )
        for rec in rows:
            pid = rec["pid"]
            pos = positions.get(pid)
            if pos is None:
                pos = PositionProfile(
                    position_id=pid,
                    name=rec.get("pname") or pid,
                    required_years=rec.get("req_years"),
                    last_updated=rec.get("last_updated"),
                    industry=rec.get("industry") or None,
                )
                positions[pid] = pos

            skill = SkillRequirement(
                skill_id=rec["sid"],
                skill_name=rec.get("sname") or rec["sid"],
                necessity=Necessity.MUST if rec.get("necessity") == "must" else Necessity.NICE,
                weight=float(rec.get("weight", 1.0) or 1.0),
                proficiency=rec.get("level"),
                source_count=int(rec.get("source_count", 1) or 1),
                is_soft=rec.get("category") == SOFT_SKILL_CATEGORY,
            )
            # 软技能退出评分（2026-08-22 拍板）：REQUIRES 边上类目=软技能的条目
            # 无论 must/nice 标注一律进独立通道，不进 must/nice 评分池
            if skill.is_soft:
                pos.soft_requirements.append(skill)
            elif skill.necessity == Necessity.MUST:
                pos.must_skills.append(skill)
            else:
                pos.nice_skills.append(skill)

        # 岗位侧软技能（Position.soft_skills，聚合层写回）：并入独立通道
        # soft_requirements（2026-08-22 拍板：不参与评分，仅差距分析展示；
        # 候选人侧 LLM 推断软技能 low_confidence ×0.5 降权仍保留——显式技术
        # 技能被标 low_confidence 时同样生效，设计文档 9.2 节）。
        soft_rows = session.run(
            """
            MATCH (p:Position)
            RETURN p.id AS pid, p.soft_skills AS soft
            """
        )
        for rec in soft_rows:
            pos = positions.get(rec["pid"])
            if pos is None:
                continue
            soft = [s for s in (rec.get("soft") or []) if s]
            pos.soft_skills = soft
            # 与 REQUIRES 软技能边同名去重（边版本带 skill_id/source_count 优先）；
            # 与 nice 同名跳过防差距列表重复（存量未回填 category 的边暂留 nice）
            existing = (
                {s.skill_name for s in pos.nice_skills}
                | {s.skill_name for s in pos.soft_requirements}
            )
            for name in soft:
                if name in existing:
                    continue
                pos.soft_requirements.append(SkillRequirement(
                    skill_id=name,
                    skill_name=name,
                    necessity=Necessity.NICE,
                    weight=_SOFT_SKILL_WEIGHT,
                    is_soft=True,
                ))
                existing.add(name)

    result = list(positions.values())
    # 预热语义向量：一次 batch encode 所有岗位技能名（含软技能独立通道——
    # 差距分析对软技能要求同样做语义匹配），评分时不再逐条前向推理
    SkillEmbedder.get().warm(
        [s.skill_name for p in result for s in (*p.must_skills, *p.nice_skills, *p.soft_requirements)]
    )
    return result


def load_positions_from_graph() -> list[PositionProfile]:
    """从图谱聚合岗位画像（进程级 TTL 缓存；加载时批量预热技能向量）。

    P1 后 API/worker 主路径走 shared_cache.load_positions_shared（Redis 版本化
    共享），本函数保留为：降级路径、集成测试与旧调用方兼容入口。
    """
    now = time.monotonic()
    cached = _positions_cache.get("positions")
    if cached is not None and now - _positions_cache["ts"] < _POSITIONS_CACHE_TTL:
        return cached

    result = _load_positions_uncached()
    _positions_cache["ts"] = now
    _positions_cache["positions"] = result
    return result
