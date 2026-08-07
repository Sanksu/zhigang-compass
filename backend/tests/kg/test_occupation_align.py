"""Occupation 对齐单元测试（设计文档 §5.1 BELONGS_TO_OCCUPATION）。

覆盖：
1. 规则命中：别名精确（忽略大小写）/ occupation.name 相等 / 别名包含 → conf 1.0
2. 语义兜底：SBERT Top-1 ≥ 0.6 → conf = 相似度；边界 0.6；规则优先于语义
3. 降级：occupations 为空/加载失败/模型不可用 → None，不抛异常
4. 入图增强：import_jd 对齐命中时 _import_jd_tx 发出 SET + MERGE；未命中无该查询
"""

import pytest

from app.services.extraction.schemas import JDExtractionResult
from app.services.kg.kg_service import import_jd
from app.services.kg.occupation_align import _SEMANTIC_THRESHOLD, OccupationAligner


class _FakeEmbedder:
    """可控相似度的 SBERT 替身：warm 记录预热名单，similarity 查表。"""

    def __init__(self, sim: dict[tuple[str, str], float] | None = None):
        self._sim = sim or {}
        self.warmed: list[str] = []
        self.broken = False

    def warm(self, names: list[str]) -> None:
        if self.broken:
            raise RuntimeError("SBERT 模型不可用")
        self.warmed = list(names)

    def similarity(self, a: str, b: str) -> float:
        return self._sim.get((a, b), self._sim.get((b, a), 0.0))


_OCCUPATIONS = [
    {"code": "15-1252", "name": "Software Developer", "aliases": ["Java Developer", "测试工程师"]},
    {"code": "13-2011", "name": "Accountants and Auditors", "aliases": ["财务会计", "审计专员"]},
    {"code": "15-1221", "name": "Computer and Information Research Scientists", "aliases": ["算法工程师"]},
]

# 语义相似度矩阵（非规则命中的岗位）
_SIM = {
    ("前端开发工程师", "Software Developer"): 0.72,
    ("前端开发工程师", "Accountants and Auditors"): 0.1,
    ("前端开发工程师", "Computer and Information Research Scientists"): 0.15,
    ("产品经理", "Software Developer"): 0.3,
    ("产品经理", "Accountants and Auditors"): 0.2,
    ("产品经理", "Computer and Information Research Scientists"): 0.2,
    ("偏门岗位", "Software Developer"): 0.55,  # 低于 0.6 阈值
}


def _make(sim: dict | None = None) -> tuple[OccupationAligner, _FakeEmbedder]:
    embedder = _FakeEmbedder(sim or _SIM)
    aligner = OccupationAligner(
        embedder=embedder, occupations_source=lambda: [dict(o) for o in _OCCUPATIONS]
    )
    return aligner, embedder


class TestRuleMatch:
    def test_alias_exact_match_case_insensitive(self):
        aligner, _ = _make()
        assert aligner.align("测试工程师") == ("15-1252", 1.0)

    def test_occupation_name_exact_match(self):
        aligner, _ = _make()
        assert aligner.align("Software Developer") == ("15-1252", 1.0)

    def test_alias_substring_in_position_name(self):
        """别名"会计"完整出现在"财务会计专员"中 → 规则命中（长度 ≥ 3）。"""
        aligner, _ = _make()
        assert aligner.align("财务会计专员") == ("13-2011", 1.0)

    def test_short_alias_not_used_for_substring(self):
        """别名过短（<3 字符）不做包含匹配，防误挂（如 "Go" 不应命中任何岗位名）。"""
        aligner, _ = _make(
            sim={("Go开发工程师", "Software Developer"): 0.9, ("Go开发工程师", "Accountants and Auditors"): 0.0}
        )
        occ = {"code": "X-0001", "name": "Unknown", "aliases": ["Go"]}
        aligner2 = OccupationAligner(embedder=_FakeEmbedder(), occupations_source=lambda: [occ])
        assert aligner2.align("Go开发工程师") is None  # 短别名不参与包含，语义无命中
        assert aligner.align("Go开发工程师") is None or aligner.align("Go开发工程师") != ("X-0001", 1.0)

    def test_rule_wins_over_semantic(self):
        """别名命中返回 1.0，即使语义对另一 occupation 分更高（规则优先）。"""
        aligner, embedder = _make(
            {("测试工程师", "Software Developer"): 0.5, ("测试工程师", "Accountants and Auditors"): 0.9}
        )
        assert aligner.align("测试工程师") == ("15-1252", 1.0)
        assert embedder.warmed == []  # 规则命中不触发语义


class TestSemanticFallback:
    def test_top1_above_threshold(self):
        aligner, embedder = _make()
        assert aligner.align("前端开发工程师") == ("15-1252", 0.72)
        assert "Software Developer" in embedder.warmed

    def test_top1_below_threshold(self):
        aligner, _ = _make()
        assert aligner.align("偏门岗位") is None

    def test_threshold_boundary(self):
        """0.6 边界：恰好 0.6 视为命中（>= 阈值）。"""
        embedder = _FakeEmbedder({("边界岗位", "Software Developer"): 0.6})
        aligner = OccupationAligner(
            embedder=embedder, occupations_source=lambda: [dict(_OCCUPATIONS[0])]
        )
        assert aligner.align("边界岗位") == ("15-1252", 0.6)
        assert _SEMANTIC_THRESHOLD == 0.6

    def test_model_unavailable_returns_none(self):
        embedder = _FakeEmbedder(_SIM)
        embedder.broken = True
        aligner = OccupationAligner(
            embedder=embedder, occupations_source=lambda: [dict(o) for o in _OCCUPATIONS]
        )
        assert aligner.align("前端开发工程师") is None  # 模型不可用 → 无语义命中


class TestDegradation:
    def test_empty_occupations_returns_none(self):
        aligner = OccupationAligner(embedder=_FakeEmbedder(), occupations_source=lambda: [])
        assert aligner.align("前端开发工程师") is None

    def test_occupations_source_error_returns_none(self):
        def boom():
            raise RuntimeError("PG/Neo4j 不可达")

        aligner = OccupationAligner(embedder=_FakeEmbedder(), occupations_source=boom)
        assert aligner.align("前端开发工程师") is None

    def test_blank_name_returns_none(self):
        aligner, _ = _make()
        assert aligner.align("") is None
        assert aligner.align("   ") is None


# ============================================================
# 入图增强（import_jd → _import_jd_tx）
# ============================================================

class _Result:
    def __init__(self, row):
        self._row = row

    def single(self):
        return self._row


class _Row:
    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]


class _FakeTx:
    def __init__(self):
        self.queries = []
        self._counters: dict[str, int] = {}

    def run(self, query, **params):
        self.queries.append((query, params))
        if "MERGE (c:Counter" in query:
            entity_type = params["entity_type"]
            self._counters[entity_type] = self._counters.get(entity_type, 0) + 1
            return _Result(_Row({"seq": self._counters[entity_type]}))
        return _Result(None)


class _FakeSession:
    def __init__(self, tx):
        self._tx = tx

    def execute_write(self, fn, *args, **kwargs):
        return fn(self._tx, *args, **kwargs)


def _extraction(position_name: str = "测试工程师") -> JDExtractionResult:
    return JDExtractionResult(position_name=position_name)


def _evidence() -> dict:
    return {
        "source": "boss",
        "source_url": "https://example.com/jd/1",
        "crawled_at": "2026-08-01T10:00:00+08:00",
        "raw_text": "负责系统开发。",
    }


@pytest.fixture
def aligner_singleton(monkeypatch):
    """替换 OccupationAligner 单例为注入 occupations_source 的实例。"""

    def _set(source, sim=None):
        monkeypatch.setattr(
            OccupationAligner,
            "_instance",
            OccupationAligner(embedder=_FakeEmbedder(sim), occupations_source=source),
        )

    return _set


class TestImportJdEnhancement:
    def test_aligned_position_creates_occupation_edge(self, aligner_singleton):
        aligner_singleton(lambda: [dict(o) for o in _OCCUPATIONS])
        tx = _FakeTx()
        import_jd(_FakeSession(tx), _extraction(), _evidence())

        occ_queries = [q for q, _ in tx.queries if "BELONGS_TO_OCCUPATION" in q]
        assert len(occ_queries) == 1
        assert "SET p.occupation_code = $code" in occ_queries[0]
        assert "MERGE (p)-[:BELONGS_TO_OCCUPATION {confidence: $conf}]->(o:Occupation {code: $code})" in occ_queries[0]
        params = [p for q, p in tx.queries if "BELONGS_TO_OCCUPATION" in q][0]
        assert params["code"] == "15-1252"
        assert params["conf"] == 1.0

    def test_semantic_aligned_uses_similarity_conf(self, aligner_singleton):
        aligner_singleton(
            lambda: [dict(o) for o in _OCCUPATIONS],
            {("前端开发工程师", "Software Developer"): 0.72},
        )
        tx = _FakeTx()
        import_jd(_FakeSession(tx), _extraction("前端开发工程师"), _evidence())
        params = [p for q, p in tx.queries if "BELONGS_TO_OCCUPATION" in q][0]
        assert params["code"] == "15-1252"
        assert params["conf"] == 0.72

    def test_unmatched_position_skips_edge(self, aligner_singleton):
        aligner_singleton(lambda: [dict(o) for o in _OCCUPATIONS], {})
        tx = _FakeTx()
        import_jd(_FakeSession(tx), _extraction("偏门岗位"), _evidence())
        assert not any("BELONGS_TO_OCCUPATION" in q for q, _ in tx.queries)

    def test_aligner_failure_does_not_block_import(self, aligner_singleton):
        def boom():
            raise RuntimeError("occupations 加载失败")

        aligner_singleton(boom)
        tx = _FakeTx()
        position_id = import_jd(_FakeSession(tx), _extraction(), _evidence())
        assert position_id == "pos_0001"  # 主链路不受影响
        assert not any("BELONGS_TO_OCCUPATION" in q for q, _ in tx.queries)
