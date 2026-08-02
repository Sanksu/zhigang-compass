"""图谱写入服务：将 raw 表数据写入 Neo4j。

两个入口：
- import_jd：JD 抽取结果 → Position/Skill/Evidence 节点 + REQUIRES/HAS_EVIDENCE 关系
- import_course：课程数据 → Course/Skill 节点 + LEARNABLE_VIA 关系

幂等设计：MERGE 语义，同源同 ID 的数据重复导入不会创建重复节点。

用法：
    from app.services.kg.kg_service import import_jd, import_course
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        course_id = import_course(session, course_dict)
        position_id = import_jd(session, extraction_result, evidence_dict)
"""

from datetime import datetime, timedelta, timezone

from neo4j import Session

from app.services.extraction.dictionary import normalize_position_name
from app.services.extraction.schemas import JDExtractionResult
from app.services.kg.id_generator import next_id


def _now() -> str:
    """当前 UTC+8 ISO8601 时间戳。"""
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


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
    - (Position)-[:REQUIRES {necessity, level}]->(Skill)
    - (Position)-[:HAS_EVIDENCE]->(Evidence)
    - (Skill)-[:MENTIONED_IN]->(Evidence)

    参数：
        session: Neo4j Session
        extraction: LLM 抽取结果（JDExtractionResult）
        evidence: 原始 JD 元数据 dict，需含 source/source_url/crawled_at/raw_text

    返回：
        Position 节点 ID（如 pos_0001）
    """
    return session.execute_write(_import_jd_tx, extraction, evidence)


def _import_jd_tx(tx, extraction: JDExtractionResult, evidence: dict) -> str:
    now = _now()
    # 岗位名归一化：合并同义重复岗位（如"前端开发/前端工程师" → "前端开发工程师"）
    position_name = normalize_position_name(extraction.position_name)

    # 1. Position：按 name 合并，不存在时分配新 ID
    result = tx.run(
        "MATCH (p:Position {name: $name}) RETURN p.id AS id",
        name=position_name,
    )
    record = result.single()
    if record:
        position_id = record["id"]
        tx.run(
            """
            MATCH (p:Position {id: $id})
            SET p.level = $level,
                p.industry = $industry,
                p.salary_range = $salary_range,
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
        tx.run(
            """
            CREATE (p:Position {
                id: $id,
                name: $name,
                level: $level,
                industry: $industry,
                salary_range: $salary_range,
                status: 'candidate',
                created_at: $now,
                updated_at: $now
            })
            """,
            id=position_id,
            name=position_name,
            level=extraction.level or "",
            industry=extraction.industry or "",
            salary_range=extraction.salary_range or "",
            now=now,
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
        raw_text=str(raw_text)[:65535] if raw_text else "",
        position_id=position_id,
        now=now,
    )

    # 3. Skills + REQUIRES 关系
    for req in extraction.requirements:
        skill_name = req.skill_name.strip()
        if not skill_name:
            continue

        # Skill：按 name 合并
        result = tx.run(
            "MATCH (s:Skill {name: $name}) RETURN s.id AS id",
            name=skill_name,
        )
        record = result.single()
        if not record:
            skill_id = next_id(tx, "Skill")
            tx.run(
                """
                CREATE (s:Skill {
                    id: $id,
                    name: $name,
                    created_at: $now
                })
                """,
                id=skill_id,
                name=skill_name,
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
            necessity=req.necessity,
            level=req.level or "",
        )

        # MENTIONED_IN 关系（Skill → Evidence）
        tx.run(
            """
            MATCH (s:Skill {name: $skill_name}), (e:Evidence {id: $evidence_id})
            MERGE (s)-[:MENTIONED_IN]->(e)
            """,
            skill_name=skill_name,
            evidence_id=evidence_id,
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
                CREATE (t:Tool {
                    id: $id,
                    name: $name,
                    category: $category,
                    vendor: $vendor,
                    created_at: $now
                })
                """,
                id=tool_id,
                name=tool_name,
                category=tool.category or "",
                vendor=tool.vendor or "",
                now=now,
            )

        tx.run(
            """
            MATCH (p:Position {id: $position_id}), (t:Tool {name: $tool_name})
            MERGE (p)-[:REQUIRES]->(t)
            """,
            position_id=position_id,
            tool_name=tool_name,
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

    # 1. Course：按 source + source_id 合并
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
        tx.run(
            """
            MATCH (c:Course {id: $id})
            SET c.name = $title,
                c.institution = $institution,
                c.platform = $platform,
                c.category = $category,
                c.description = $description,
                c.rating = $rating,
                c.enrollment = $enrollment,
                c.duration = $duration,
                c.source_url = $source_url,
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
        tx.run(
            """
            CREATE (c:Course {
                id: $id,
                source: $source,
                source_id: $source_id,
                name: $title,
                institution: $institution,
                platform: $platform,
                category: $category,
                description: $description,
                rating: $rating,
                enrollment: $enrollment,
                duration: $duration,
                source_url: $source_url,
                created_at: $now,
                updated_at: $now
            })
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

    # 2. Skills + LEARNABLE_VIA 关系
    skills = course_data.get("skills", [])
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]

    for skill_name in skills:
        if not skill_name or not skill_name.strip():
            continue
        skill_name = skill_name.strip()

        # Skill：按 name 合并
        result = tx.run(
            "MATCH (s:Skill {name: $name}) RETURN s.id AS id",
            name=skill_name,
        )
        record = result.single()
        if not record:
            skill_id = next_id(tx, "Skill")
            tx.run(
                """
                CREATE (s:Skill {
                    id: $id,
                    name: $name,
                    created_at: $now
                })
                """,
                id=skill_id,
                name=skill_name,
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
