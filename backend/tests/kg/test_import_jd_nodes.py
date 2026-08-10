"""import_jd 学历/证书节点入图测试（设计文档 5.1/5.2：Education/Certification 实体入图）。

用 FakeSession/FakeTx 桩模拟 Neo4j 会话（参照 tests/kg/test_aggregation.py 风格），
验证 _import_jd_tx 对 JDExtractionResult 的 education/certifications 各 MERGE 节点 +
Position-[:REQUIRES {necessity: 'must'}] 关系，且同名实体重复导入 ID 稳定（MERGE 幂等）。
"""

from app.services.extraction.schemas import (
    CertificationExtracted,
    EducationExtracted,
    JDExtractionResult,
    REQUIRESRelation,
    SkillExtracted,
)
from app.services.kg.kg_service import import_jd, _import_course_tx


class _Result:
    def __init__(self, row):
        self._row = row

    def single(self):
        return self._row


class _Row:
    """dict 风格行：支持 record["id"] / record["seq"] 下标访问。"""

    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]


class _FakeTx:
    """事务桩：收集 run 调用；Counter 自增返回 seq，MATCH 查询视为无命中。"""

    def __init__(self):
        self.queries = []
        self._counters: dict[str, int] = {}

    def run(self, query, **params):
        self.queries.append((query, params))
        if "MERGE (c:Counter" in query:
            entity_type = params["entity_type"]
            self._counters[entity_type] = self._counters.get(entity_type, 0) + 1
            return _Result(_Row({"seq": self._counters[entity_type]}))
        # 模拟 Position MERGE ON CREATE 语义：返回分配（或已存在）的 id
        if "MERGE (p:Position" in query:
            return _Result(_Row({"id": params["id"]}))
        return _Result(None)


class _FakeSession:
    def __init__(self, tx):
        self._tx = tx

    def execute_write(self, fn, *args, **kwargs):
        return fn(self._tx, *args, **kwargs)


def _extraction() -> JDExtractionResult:
    return JDExtractionResult(
        position_name="测试工程师",  # 标准岗位名族，归一化不触达 Neo4j
        education=EducationExtracted(level="本科", major="计算机科学"),
        certifications=[CertificationExtracted(name="AWS 数据类认证")],
    )


def _evidence() -> dict:
    return {
        "source": "boss",
        "source_url": "https://example.com/jd/1",
        "crawled_at": "2026-08-01T10:00:00+08:00",
        "raw_text": "任职要求：本科及以上，计算机相关专业，持有 AWS 数据类认证。",
    }


class TestImportJdEducationCertification:
    def test_education_node_and_requires_created(self):
        tx = _FakeTx()
        position_id = import_jd(_FakeSession(tx), _extraction(), _evidence())
        assert position_id == "pos_0001"

        edu_nodes = [p for q, p in tx.queries if "MERGE (e:Education" in q]
        assert len(edu_nodes) == 1
        assert edu_nodes[0]["name"] == "本科 · 计算机科学"
        assert edu_nodes[0]["level"] == "本科"
        assert edu_nodes[0]["major"] == "计算机科学"
        assert edu_nodes[0]["id"].startswith("ed_")

        edu_rel = [q for q, _ in tx.queries
                   if "MERGE (p)-[:REQUIRES" in q and "Education" in q]
        assert len(edu_rel) == 1
        assert "necessity: 'must'" in edu_rel[0]

    def test_certification_node_and_requires_created(self):
        tx = _FakeTx()
        import_jd(_FakeSession(tx), _extraction(), _evidence())

        cert_nodes = [p for q, p in tx.queries if "MERGE (c:Certification" in q]
        assert len(cert_nodes) == 1
        assert cert_nodes[0]["name"] == "AWS 数据类认证"
        assert cert_nodes[0]["id"].startswith("ce_")

        cert_rel = [q for q, _ in tx.queries
                    if "MERGE (p)-[:REQUIRES" in q and "Certification" in q]
        assert len(cert_rel) == 1

    def test_same_education_import_is_idempotent(self):
        """同名学历/证书要求重复导入生成相同 ID（name 哈希），MERGE 幂等不漂移。"""
        tx = _FakeTx()
        session = _FakeSession(tx)
        import_jd(session, _extraction(), _evidence())
        import_jd(session, _extraction(), _evidence())
        edu_ids = [p["id"] for q, p in tx.queries if "MERGE (e:Education" in q]
        cert_ids = [p["id"] for q, p in tx.queries if "MERGE (c:Certification" in q]
        assert len(set(edu_ids)) == 1
        assert len(set(cert_ids)) == 1

    def test_missing_education_and_certifications_skipped(self):
        ext = JDExtractionResult(position_name="测试工程师", education=None, certifications=[])
        tx = _FakeTx()
        import_jd(_FakeSession(tx), ext, _evidence())
        assert not any("Education" in q for q, _ in tx.queries)
        assert not any("Certification" in q for q, _ in tx.queries)


class TestImportJdSkillNormalization:
    """import_jd 技能名归一化：旧快照异构名（P1-1 前抽取）合并到规范 Skill 节点。

    重建（rebuild_graph）直接重放 jd_raw 快照，快照技能名可能是 Vue3/reactjs 等
    别名；import_jd 归一化后才能命中规范节点，防止重建出旧名节点回退 P1-3 合并。
    """

    def _run(self, skill_names: list[str]) -> list[tuple[str, dict]]:
        tx = _FakeTx()
        ext = JDExtractionResult(
            position_name="测试工程师",
            requirements=[REQUIRESRelation(skill_name=n, necessity="must") for n in skill_names],
        )
        import_jd(_FakeSession(tx), ext, _evidence())
        return [(q, p) for q, p in tx.queries if "MERGE (s:Skill" in q]

    def test_alias_normalized_to_canonical_name(self):
        skill_merges = self._run(["Vue3"])
        assert len(skill_merges) == 1
        assert skill_merges[0][1]["name"] == "Vue.js"

    def test_no_orphan_alias_node(self):
        # 别名（Vue3）不入图，只建规范节点（Vue.js）——重建不产生旧名节点
        skill_merges = self._run(["Vue3", "Vue.js"])
        names = {p["name"] for _, p in skill_merges}
        assert names == {"Vue.js"}

    def test_whitelist_word_preserved(self):
        # 白名单词整体保护（clean_skill_name），不被剥成泛词碎片
        skill_merges = self._run(["操作系统"])
        assert skill_merges[0][1]["name"] == "操作系统"

    def test_stopword_dropped(self):
        # 归一化后为空（"系统"→""）直接跳过，不建节点
        skill_merges = self._run(["系统"])
        assert skill_merges == []

    def test_stopword_preserved_after_clean_dropped(self):
        # 剥后缀剥不掉的旧泛词（"嵌入式"）按黑名单剔除，不建节点
        skill_merges = self._run(["嵌入式"])
        assert skill_merges == []


class TestImportJdSkillsOnlyEdges:
    """P1-2 技能边静默丢失修复：extraction.skills 中未进 requirements 的技能
    （LLM 只给技能列表未细分 must/nice 的旧快照）也建 Skill 节点 +
    REQUIRES{nice} 边，与聚合 _position_skills 的「requirements 优先、skills
    未覆盖以 nice 并入」口径对齐；否则聚合输出 nice 边因无 Skill 节点
    MATCH 不上而静默丢失。
    """

    def _tx(self, ext: JDExtractionResult) -> _FakeTx:
        tx = _FakeTx()
        import_jd(_FakeSession(tx), ext, _evidence())
        return tx

    def _requires(self, tx):
        # 技能边查询为 MERGE (p)-[r:REQUIRES]；学历/证书边为 [:REQUIRES] 形式，不匹配
        return [p for q, p in tx.queries if "MERGE (p)-[r:REQUIRES]" in q]

    def _evidenced_by(self, tx):
        return [p for q, p in tx.queries if "MERGE (s)-[:EVIDENCED_BY]" in q]

    def test_skills_only_skill_gets_nice_requires_edge(self):
        # 无 requirements 的旧快照：skills=[Vue3] → 建 Vue.js 节点 + REQUIRES{nice}
        ext = JDExtractionResult(
            position_name="测试工程师",
            skills=[SkillExtracted(name="Vue3")],
        )
        tx = self._tx(ext)
        merges = [p for q, p in tx.queries if "MERGE (s:Skill" in q]
        assert len(merges) == 1
        assert merges[0]["name"] == "Vue.js"
        rels = self._requires(tx)
        assert len(rels) == 1
        assert rels[0]["necessity"] == "nice"
        assert rels[0]["level"] == ""
        # skills-only 技能同样有证据支撑边
        assert self._evidenced_by(tx)[0]["skill_name"] == "Vue.js"

    def test_skill_in_requirements_not_duplicated(self):
        # requirements 已含 Vue.js(must)，skills 重复出现 → 不重复建 nice 边
        ext = JDExtractionResult(
            position_name="测试工程师",
            requirements=[REQUIRESRelation(skill_name="Vue.js", necessity="must")],
            skills=[SkillExtracted(name="Vue.js")],
        )
        tx = self._tx(ext)
        assert len([q for q, _ in tx.queries if "MERGE (s:Skill" in q]) == 1
        assert len(self._requires(tx)) == 1
        assert self._requires(tx)[0]["necessity"] == "must"
        assert len(self._evidenced_by(tx)) == 1

    def test_skills_extra_beyond_requirements_merged(self):
        # requirements=[Java must] + skills=[Java, Vue3] → Java must + Vue.js nice
        ext = JDExtractionResult(
            position_name="测试工程师",
            requirements=[REQUIRESRelation(skill_name="Java", necessity="must")],
            skills=[SkillExtracted(name="Java"), SkillExtracted(name="Vue3")],
        )
        tx = self._tx(ext)
        assert len([q for q, _ in tx.queries if "MERGE (s:Skill" in q]) == 2
        by_name = {p["skill_name"]: p for p in self._requires(tx)}
        assert by_name["Java"]["necessity"] == "must"
        assert by_name["Vue.js"]["necessity"] == "nice"
        assert len(self._evidenced_by(tx)) == 2


class TestImportCourseSkillNormalization:
    """回归：课程入图技能名须与 JD 侧一致归一化（canonical_skill_name）。

    数据审查发现：课程源技能名是英文原始名（"Prompt Engineering"），此前
    _import_course_tx 仅 strip 不归一化，导致与 JD 侧规范节点（"提示工程"）
    分裂成两个 Skill 节点，LEARNABLE_VIA 与 REQUIRES 无法在同一节点汇聚。
    """

    def test_course_skill_canonicalized(self):
        tx = _FakeTx()
        course_data = {
            "course_id": "course-1",
            "title": "Prompt Engineering 课程",
            "source": "coursera",
            "skills": ["Prompt Engineering", "  Vue3  ", "人工智能"],
        }
        _import_course_tx(tx, course_data)
        merges = [p for q, p in tx.queries if "MERGE (s:Skill" in q]
        names = {m["name"] for m in merges}
        # "Prompt Engineering" 归一化为 "提示工程"，Vue3 归一化为 "Vue.js"
        assert "提示工程" in names
        assert "Vue.js" in names
        assert "Prompt Engineering" not in names
        assert "Vue3" not in names
