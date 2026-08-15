"""图谱写入服务：将 raw 表数据写入 Neo4j。

两个入口：
- import_jd：JD 抽取结果 → Position/Skill/Evidence 节点 + REQUIRES/EVIDENCED_BY 关系
- import_course：课程数据 → Course/Skill 节点 + LEARNABLE_VIA 关系

幂等设计：MERGE 语义，同源同 ID 的数据重复导入不会创建重复节点。
例外：Evidence 节点按「每个 JD 原文对应一个证据」每次导入新建（CREATE），
重复导入同一 JD 会产生新的证据节点与边——审计口径为每批导入留证，非幂等。

用法：
    from app.services.kg.kg_service import import_jd, import_course
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        course_id = import_course(session, course_dict)
        position_id = import_jd(session, extraction_result, evidence_dict)
"""

import hashlib
from datetime import datetime, timedelta, timezone

from neo4j import Session

from app.services.extraction.dictionary import normalize_position_name, skill_category
from app.services.extraction.post_processor import _is_valid_skill_name, canonical_skill_name
from app.services.extraction.schemas import JDExtractionResult
from app.services.kg.id_generator import PREFIX_MAP, next_id


def _now() -> str:
    """当前 UTC+8 ISO8601 时间戳。"""
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def _stable_id(entity_type: str, name: str) -> str:
    """基于实体名生成稳定 ID（`{prefix}_{sha1(name)[:8]}`）。

    Education/Certification 按内容归并（同一学历/证书要求跨 JD 复用同一节点，
    schema.cypher §5：REQUIRES 目标含 Education/Certification）：用 name 哈希
    派生 ID，重复导入同名实体 ID 一致，MERGE 天然幂等、不产生 ID 漂移
    （区别于 Position/Skill 的 Counter 自增，见 id_generator.PREFIX_MAP）。
    """
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{PREFIX_MAP[entity_type]}_{digest}"


# ============================================================
# JD 入图
# ============================================================

def import_jd(
    session: Session,
    extraction: JDExtractionResult,
    evidence: dict,
) -> str:
    """JD 抽取结果入图。

    创建的节点和关系：
    - (Position {name: extraction.position_name})
    - (Evidence {source, source_url, crawled_at, raw_text})
    - (Skill {name: ...}) × N
    - (Education {name: level·major}) / (Certification {name: ...})（学历/证书要求）
    - (Position)-[:REQUIRES {necessity, level}]->(Skill)
    - (Position)-[:REQUIRES {necessity: 'must'}]->(Education|Certification)
    - (Position)-[:HAS_EVIDENCE]->(Evidence)（辅助维护边：岗位→原始 JD 证据，
      供 cleanup_graph 岗位合并时证据重连；产品证据查询统一走 EVIDENCED_BY）
    - (Skill)-[:EVIDENCED_BY]->(Evidence)（设计文档 §5.1：技能由证据支撑）
    - (Position)-[:BELONGS_TO_OCCUPATION]->(Occupation)（规则优先 + 语义兜底对齐，
      设计文档 §5.1；对齐未命中/模型不可用时无此边，不阻塞入图）

    参数：
        session: Neo4j Session
        extraction: LLM 抽取结果（JDExtractionResult）
        evidence: 原始 JD 元数据 dict，需含 source/source_url/crawled_at/raw_text

    返回：
        Position 节点 ID（如 pos_0001）
    """
    # 岗位名归一化（纯规则）在事务外执行，避免嵌套 Neo4j 会话。与聚合链路共用
    # normalize_position_name，保证入图/聚合岗位名口径一致（修复：语义兜底对齐
    # 结果与聚合规则不一致，导致聚合写回 MATCH 不上图节点）。
    from app.services.extraction.dictionary import normalize_position_name

    # 传 skills 保证兜底族岗位（软件开发工程师/算法工程师等）按技能路由到细分族，
    # 与 batch_extract 快照、聚合链路口径一致；否则二次归一化会把合法路由结果
    # （如纯通用算法技能路由到的"算法工程师"）清空为不入图
    extraction.position_name = normalize_position_name(
        extraction.position_name,
        skills=[s.name for s in (extraction.skills or [])],
    )
    # Occupation 对齐也在事务外执行（语义嵌入耗时，避免长事务）；
    # 任何失败降级为无 occupation 边，不阻塞入图主链路。
    occupation: tuple[str, float] | None = None
    if extraction.position_name:
        try:
            from app.services.kg.occupation_align import OccupationAligner

            occupation = OccupationAligner.get().align(extraction.position_name)
        except Exception:
            occupation = None
    return session.execute_write(_import_jd_tx, extraction, evidence, occupation)


def _import_skill_edge(
    tx,
    position_id: str,
    skill_name: str,
    *,
    necessity: str,
    level: str,
    evidence_id: str,
    now: str,
) -> None:
    """建 Skill 节点 + Position-[:REQUIRES {necessity, level}]->Skill +
    Skill-[:EVIDENCED_BY]->Evidence。

    requirements 与 skills 两路共用：Skill 按 name 合并（MERGE ON CREATE 兜底
    并发竞态），REQUIRES 属性覆盖写，EVIDENCED_BY 幂等。name 已由调用方归一化。
    """
    # Skill：按 name 合并（MERGE ON CREATE 兜底并发竞态，防重复建节点）
    result = tx.run(
        "MATCH (s:Skill {name: $name}) RETURN s.id AS id",
        name=skill_name,
    )
    record = result.single()
    if not record:
        skill_id = next_id(tx, "Skill")
        tx.run(
            """
            MERGE (s:Skill {name: $name})
            ON CREATE SET s.id = $id,
                s.name = $name,
                s.category = $category,
                s.created_at = $now,
                s.first_seen = $now
            """,
            id=skill_id,
            name=skill_name,
            category=skill_category(skill_name),
            now=now,
        )

    # REQUIRES 关系（Position → Skill）
    tx.run(
        """
        MATCH (p:Position {id: $position_id}), (s:Skill {name: $skill_name})
        MERGE (p)-[r:REQUIRES]->(s)
        SET r.necessity = $necessity,
            r.level = $level
        """,
        position_id=position_id,
        skill_name=skill_name,
        necessity=necessity,
        level=level,
    )

    # EVIDENCED_BY 关系（Skill → Evidence，设计文档 §5.1）
    tx.run(
        """
        MATCH (s:Skill {name: $skill_name}), (e:Evidence {id: $evidence_id})
        MERGE (s)-[:EVIDENCED_BY]->(e)
        """,
        skill_name=skill_name,
        evidence_id=evidence_id,
    )


def _import_jd_tx(
    tx,
    extraction: JDExtractionResult,
    evidence: dict,
    occupation: tuple[str, float] | None = None,
) -> str:
    now = _now()
    # 岗位名归一化：合并同义重复岗位（如"前端开发/前端工程师" → "前端开发工程师"）。
    # 传 skills：兜底族岗位二次归一化（batch_extract 已带 skills 路由一次）保持
    # 路由结果稳定——纯通用算法技能路由到的"算法工程师"不会被清空
    position_name = normalize_position_name(
        extraction.position_name,
        skills=[s.name for s in (extraction.skills or [])],
    )
    if not position_name:
        # 空抽取（正文质量差导致无岗位名）不入图，避免产生空岗位节点
        return ""

    # 1. Position：按 name 合并，不存在时分配新 ID。
    #    先 MATCH 快查避免无谓消耗 Counter；并发下两个事务同时未命中时，
    #    MERGE ON CREATE 兜底保证只产生一个节点（RETURN 拿回实际 id）。
    result = tx.run(
        "MATCH (p:Position {name: $name}) RETURN p.id AS id",
        name=position_name,
    )
    record = result.single()
    if record:
        position_id = record["id"]
        # SET 非空保护：新抽取结果缺字段（空串）时不覆盖已有值，
        # 避免低质量 JD 把已有岗位的 level/industry/salary 洗空
        tx.run(
            """
            MATCH (p:Position {id: $id})
            SET p.level = CASE WHEN $level <> '' THEN $level ELSE p.level END,
                p.industry = CASE WHEN $industry <> '' THEN $industry ELSE p.industry END,
                p.salary_range = CASE WHEN $salary_range <> '' THEN $salary_range ELSE p.salary_range END,
                p.updated_at = $now
            """,
            id=position_id,
            level=extraction.level or "",
            industry=extraction.industry or "",
            salary_range=extraction.salary_range or "",
            now=now,
        )
    else:
        position_id = next_id(tx, "Position")
        result = tx.run(
            """
            MERGE (p:Position {name: $name})
            ON CREATE SET p.id = $id,
                p.name = $name,
                p.level = $level,
                p.industry = $industry,
                p.salary_range = $salary_range,
                p.status = 'active',
                p.created_at = $now,
                p.updated_at = $now
            RETURN p.id AS id
            """,
            id=position_id,
            name=position_name,
            level=extraction.level or "",
            industry=extraction.industry or "",
            salary_range=extraction.salary_range or "",
            now=now,
        )
        record = result.single()
        if record:
            position_id = record["id"]

    # 1.5 Occupation 归属（设计文档 §5.1 (Position)-[:BELONGS_TO_OCCUPATION]->(Occupation)）。
    # occupation 由 import_jd 事务外对齐（规则优先 + SBERT 语义兜底），未命中为 None 不入边。
    if occupation is not None:
        occ_code, occ_conf = occupation
        tx.run(
            """
            MATCH (p:Position {id: $position_id})
            SET p.occupation_code = $code
            MERGE (p)-[:BELONGS_TO_OCCUPATION {confidence: $conf}]->(o:Occupation {code: $code})
            """,
            position_id=position_id,
            code=occ_code,
            conf=occ_conf,
        )

    # 2. Evidence：每次创建新节点（每个 JD 原文对应一个 Evidence）
    evidence_id = next_id(tx, "Evidence")
    raw_text = evidence.get("raw_text", "")
    tx.run(
        """
        CREATE (e:Evidence {
            id: $id,
            source: $source,
            source_url: $source_url,
            crawled_at: $crawled_at,
            raw_text: $raw_text,
            created_at: $now
        })
        WITH e
        MATCH (p:Position {id: $position_id})
        CREATE (p)-[:HAS_EVIDENCE]->(e)
        """,
        id=evidence_id,
        source=evidence.get("source", ""),
        source_url=evidence.get("source_url", ""),
        crawled_at=evidence.get("crawled_at", ""),
        # 08-14：移除 65535 截断（初始化模板遗留，Neo4j 无属性长度限制），
        # Evidence 保留 JD 原文完整备份供证据追溯
        raw_text=str(raw_text) if raw_text else "",
        position_id=position_id,
        now=now,
    )

    # 3. Skills + REQUIRES 关系
    # 技能名与抽取链路一致归一化（normalize_skill → clean_skill_name → 黑名单过滤）：
    # 重建时 jd_raw 快照可能是 P1-1/P1-2 扩充前的旧值（Vue3/reactjs、嵌入式/前端等
    # 泛词），归一化后才能合并到规范节点，避免重建出旧名/泛词 Skill 使合并效果回退。
    # requirements 与 skills 两路都入图（P1-2）：抽取可能只给技能列表未细分
    # must/nice，聚合 _position_skills 会把未进 requirements 的技能以 nice 并入，
    # 入图侧不同步会因无 Skill 节点导致聚合 nice 边静默丢失。
    handled_skills: set[str] = set()
    for req in extraction.requirements:
        skill_name = canonical_skill_name(req.skill_name)
        if not skill_name or not _is_valid_skill_name(skill_name):
            continue
        handled_skills.add(skill_name)
        _import_skill_edge(
            tx, position_id, skill_name,
            necessity=req.necessity, level=req.level or "",
            evidence_id=evidence_id, now=now,
        )

    for skill in extraction.skills:
        skill_name = canonical_skill_name(skill.name)
        if not skill_name or not _is_valid_skill_name(skill_name):
            continue
        if skill_name in handled_skills:
            continue
        handled_skills.add(skill_name)
        _import_skill_edge(
            tx, position_id, skill_name,
            necessity="nice", level="",
            evidence_id=evidence_id, now=now,
        )

    # 4. Tools（如果有）
    for tool in extraction.tools:
        tool_name = tool.name.strip()
        if not tool_name:
            continue

        result = tx.run(
            "MATCH (t:Tool {name: $name}) RETURN t.id AS id",
            name=tool_name,
        )
        record = result.single()
        if not record:
            tool_id = next_id(tx, "Tool")
            tx.run(
                """
                MERGE (t:Tool {name: $name})
                ON CREATE SET t.id = $id,
                    t.name = $name,
                    t.category = $category,
                    t.vendor = $vendor,
                    t.created_at = $now
                """,
                id=tool_id,
                name=tool_name,
                category=tool.category or "",
                vendor=tool.vendor or "",
                now=now,
            )

        # JD 中出现的工具均视为必备要求，显式写 necessity='must'
        # （与 Education/Certification 口径一致，避免消费方依赖兜底默认值）
        tx.run(
            """
            MATCH (p:Position {id: $position_id}), (t:Tool {name: $tool_name})
            MERGE (p)-[r:REQUIRES]->(t)
            SET r.necessity = 'must'
            """,
            position_id=position_id,
            tool_name=tool_name,
        )

    # 5. Education（学历/专业要求节点，schema.cypher §5：REQUIRES 目标含 Education）。
    # JD 侧抽取结果无 institution 字段（JDExtractionResult.education 仅 level/major），
    # 故节点不含 institution；name 取 level·major 组合，同名要求跨 JD 归并到同一节点。
    if extraction.education:
        edu = extraction.education
        edu_name = " · ".join(part for part in (edu.level or "", edu.major or "") if part)
        if edu_name:
            education_id = _stable_id("Education", edu_name)
            tx.run(
                """
                MERGE (e:Education {id: $id})
                ON CREATE SET e.created_at = $now
                SET e.name = $name, e.level = $level, e.major = $major
                """,
                id=education_id,
                name=edu_name,
                level=edu.level or "",
                major=edu.major or "",
                now=now,
            )
            # Position → REQUIRES → Education（学历要求为 JD 必备项）
            tx.run(
                """
                MATCH (p:Position {id: $position_id}), (e:Education {id: $education_id})
                MERGE (p)-[:REQUIRES {necessity: 'must'}]->(e)
                """,
                position_id=position_id,
                education_id=education_id,
            )

    # 6. Certifications（证书要求节点，schema.cypher §5：REQUIRES 目标含 Certification）。
    # JD 侧抽取结果无 issuer 字段（JDExtractionResult.certifications 仅 name），
    # 故节点不含 issuer；同名证书要求跨 JD 归并到同一节点。
    for cert in extraction.certifications:
        cert_name = cert.name.strip()
        if not cert_name:
            continue
        certification_id = _stable_id("Certification", cert_name)
        tx.run(
            """
            MERGE (c:Certification {id: $id})
            ON CREATE SET c.created_at = $now
            SET c.name = $name
            """,
            id=certification_id,
            name=cert_name,
            now=now,
        )
        # Position → REQUIRES → Certification（证书要求为 JD 必备项）
        tx.run(
            """
            MATCH (p:Position {id: $position_id}), (c:Certification {id: $certification_id})
            MERGE (p)-[:REQUIRES {necessity: 'must'}]->(c)
            """,
            position_id=position_id,
            certification_id=certification_id,
        )

    return position_id


# ============================================================
# Course 入图
# ============================================================

def import_course(session: Session, course_data: dict) -> str:
    """课程数据入图。

    创建的节点和关系：
    - (Course {source, source_id, name, platform, ...})
    - (Skill {name: ...}) × N（来自 course_data['skills']）
    - (Skill)-[:LEARNABLE_VIA]->(Course)

    参数：
        session: Neo4j Session
        course_data: CourseItem 的 dict（来自 course_raw.snapshot）

    返回：
        Course 节点 ID（如 co_0001）
    """
    return session.execute_write(_import_course_tx, course_data)


def _import_course_tx(tx, course_data: dict) -> str:
    now = _now()
    source = course_data.get("source", "")
    source_id = course_data.get("source_id", "")

    # 1. Course：按 source + source_id 合并。
    #    先 MATCH 快查避免无谓消耗 Counter；并发下 MERGE ON CREATE 兜底防重复建节点。
    result = tx.run(
        """
        MATCH (c:Course {source: $source, source_id: $source_id})
        RETURN c.id AS id
        """,
        source=source,
        source_id=source_id,
    )
    record = result.single()

    if record:
        course_id = record["id"]
        # SET 非空保护：新数据缺字段（空串/0）时不覆盖已有值，
        # 避免低质量快照把课程评分/时长等已有信息洗空
        tx.run(
            """
            MATCH (c:Course {id: $id})
            SET c.name = CASE WHEN $title <> '' THEN $title ELSE c.name END,
                c.institution = CASE WHEN $institution <> '' THEN $institution ELSE c.institution END,
                c.platform = CASE WHEN $platform <> '' THEN $platform ELSE c.platform END,
                c.category = CASE WHEN $category <> '' THEN $category ELSE c.category END,
                c.description = CASE WHEN $description <> '' THEN $description ELSE c.description END,
                c.rating = CASE WHEN $rating <> 0 THEN $rating ELSE c.rating END,
                c.enrollment = CASE WHEN $enrollment <> 0 THEN $enrollment ELSE c.enrollment END,
                c.duration = CASE WHEN $duration <> '' THEN $duration ELSE c.duration END,
                c.source_url = CASE WHEN $source_url <> '' THEN $source_url ELSE c.source_url END,
                c.updated_at = $now
            """,
            id=course_id,
            title=course_data.get("title", ""),
            institution=course_data.get("institution", ""),
            platform=course_data.get("platform", ""),
            category=course_data.get("category", ""),
            description=course_data.get("description", ""),
            rating=course_data.get("rating", 0.0),
            enrollment=course_data.get("enrollment", 0),
            duration=course_data.get("duration", ""),
            source_url=course_data.get("source_url", ""),
            now=now,
        )
    else:
        course_id = next_id(tx, "Course")
        result = tx.run(
            """
            MERGE (c:Course {source: $source, source_id: $source_id})
            ON CREATE SET c.id = $id,
                c.source = $source,
                c.source_id = $source_id,
                c.name = $title,
                c.institution = $institution,
                c.platform = $platform,
                c.category = $category,
                c.description = $description,
                c.rating = $rating,
                c.enrollment = $enrollment,
                c.duration = $duration,
                c.source_url = $source_url,
                c.created_at = $now,
                c.updated_at = $now
            RETURN c.id AS id
            """,
            id=course_id,
            source=source,
            source_id=source_id,
            title=course_data.get("title", ""),
            institution=course_data.get("institution", ""),
            platform=course_data.get("platform", ""),
            category=course_data.get("category", ""),
            description=course_data.get("description", ""),
            rating=course_data.get("rating", 0.0),
            enrollment=course_data.get("enrollment", 0),
            duration=course_data.get("duration", ""),
            source_url=course_data.get("source_url", ""),
            now=now,
        )
        record = result.single()
        if record:
            course_id = record["id"]

    # 2. Skills + LEARNABLE_VIA 关系
    skills = course_data.get("skills", [])
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]

    for skill_name in skills:
        if not skill_name or not skill_name.strip():
            continue
        # 技能名与 JD 入图侧一致归一化（canonical_skill_name → 黑名单过滤）：
        # 课程源技能名可能是英文原始名（"Prompt Engineering"），不归一化会与
        # JD 侧规范节点（"提示工程"）分裂成两个 Skill 节点，导致 LEARNABLE_VIA
        # 与 REQUIRES 无法在同一节点汇聚（数据审查 major：课程入图技能名口径）。
        skill_name = canonical_skill_name(skill_name.strip())
        if not skill_name or not _is_valid_skill_name(skill_name):
            continue

        # Skill：按 name 合并（MERGE ON CREATE 兜底并发竞态，防重复建节点）
        result = tx.run(
            "MATCH (s:Skill {name: $name}) RETURN s.id AS id",
            name=skill_name,
        )
        record = result.single()
        if not record:
            skill_id = next_id(tx, "Skill")
            tx.run(
                """
                MERGE (s:Skill {name: $name})
                ON CREATE SET s.id = $id,
                    s.name = $name,
                    s.category = $category,
                    s.created_at = $now,
                    s.first_seen = $now
                """,
                id=skill_id,
                name=skill_name,
                category=skill_category(skill_name),
                now=now,
            )

        # LEARNABLE_VIA 关系（Skill → Course）
        tx.run(
            """
            MATCH (s:Skill {name: $skill_name}), (c:Course {id: $course_id})
            MERGE (s)-[:LEARNABLE_VIA]->(c)
            """,
            skill_name=skill_name,
            course_id=course_id,
        )

    return course_id
