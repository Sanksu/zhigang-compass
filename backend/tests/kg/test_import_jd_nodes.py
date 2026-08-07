"""import_jd 学历/证书节点入图测试（设计文档 5.1/5.2：Education/Certification 实体入图）。

用 FakeSession/FakeTx 桩模拟 Neo4j 会话（参照 tests/kg/test_aggregation.py 风格），
验证 _import_jd_tx 对 JDExtractionResult 的 education/certifications 各 MERGE 节点 +
Position-[:REQUIRES {necessity: 'must'}] 关系，且同名实体重复导入 ID 稳定（MERGE 幂等）。
"""

from app.services.extraction.schemas import (
    CertificationExtracted,
    EducationExtracted,
    JDExtractionResult,
)
from app.services.kg.kg_service import import_jd


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
        position_name="测试工程师",  # 标准岗位名族，PositionAligner 不触达 Neo4j
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
