"""三源检索 → 诊断报告生成端到端测试（设计文档 §7.2.3 / §6.4）。

以真实职业名「人工智能工程师」驱动完整链路：
  三源 occupations（O*NET / 人社部大典 / LinkedIn）→ 通用 RAG 检索
  （retrieve_context）→ 诊断报告生成（generate_diagnosis，注入 RAG 上下文）。

验证点：
- 三源命中：同一职业名在 occupations 三源（source 字段区分）均产生命中，
  evidence_id 覆盖 occupation:{code} 三种来源；
- 链路贯通：检索出的岗位定义/历史诊断进入诊断 prompt 的【图谱参考上下文】；
- evidence_id 可追溯：LLM 返回的差距建议 evidence_id 来自检索上下文
  （§6.4 生成约束：仅基于证据生成、虚构引用拦截）。
"""

import asyncio

from app.models.business import DiagnosisReportRecord, DiscoveryCandidate, Occupation
from app.services.diagnosis.generator import generate_diagnosis
from app.services.diagnosis.schemas import DiagnosisReport, GapAdvice
from app.services.rag.retrieval import retrieve_context

# 端到端用例的职业名称：人社部大典「人工智能工程技术人员」的常见招聘称谓
_POSITION = "人工智能工程师"


def _tri_source_occupations() -> list[Occupation]:
    """三源权威岗位数据（同一职业名可从三源命中）。

    - onet：英文名经语义路命中（embedder 恒高分）
    - hrss：人社部大典数字技术职业，aliases 含招聘称谓
    - linkedin：Emerging Jobs 报告岗位，aliases 含招聘称谓
    """
    return [
        Occupation(
            code="15-1252.00",
            name="Software Developers",
            category="Computer and Mathematical",
            definition="Design and develop software systems.",
            aliases=["Software Engineers"],
            source="onet",
        ),
        Occupation(
            code="2-02-38-01",
            name="人工智能工程技术人员",
            category="数字技术工程技术人员",
            definition="从事人工智能相关算法、深度学习技术的分析、研究、开发，设计、优化、运维、管理和应用人工智能系统。",
            aliases=["人工智能工程师", "AI工程师"],
            source="hrss",
        ),
        Occupation(
            code="LI-0001",
            name="AI Engineer",
            category="Artificial Intelligence",
            definition="Designs and deploys AI/LLM solutions including RAG pipelines and agent systems.",
            aliases=["人工智能工程师"],
            source="linkedin",
        ),
    ]


def _diagnosis_data() -> dict:
    """人岗比对结果快照（与 tests/matching/test_diagnosis_generator 的 data 同构）。"""
    return {
        "position_name": _POSITION,
        "total_score": 0.62,
        "must_score": 0.5,
        "nice_score": 0.8,
        "exp_score": 0.75,
        "matched_must": ["Python"],
        "missing_must": ["大模型微调"],
        "gaps": [
            {"skill": "大模型微调", "gap_type": "missing", "priority": "high"},
            {"skill": "RAG 工程化", "gap_type": "weak", "priority": "medium"},
        ],
        "learning_path": [
            {
                "skill": "大模型微调",
                "estimated_hours": 40,
                "courses": [{"title": "LLM 微调实践", "platform": "MOOC"}],
            },
        ],
        "evidence_refs": [
            {"skill": "大模型微调", "source": "JD#12", "url": "https://example.com/jd/12"},
        ],
    }


class _FakeDb:
    """按调用次序返回批次的假 AsyncSession（scalars → all 消费一批）。"""

    def __init__(self, *batches):
        self._batches = list(batches)
        self._cur = []

    async def scalars(self, stmt):
        self._cur = self._batches.pop(0) if self._batches else []
        return self

    def all(self):
        return self._cur


class _FakeEmbedder:
    """固定相似度的假 embedder（embed 返回 384 维向量，语义路恒高分命中）。"""

    def embed(self, text):
        return [0.1] * 384

    def similarity(self, a, b):
        return 0.9


class _FakeLLM:
    """固定返回诊断报告的假 LLM 链，记录最近一次 prompt 供断言。"""

    def __init__(self, report: DiagnosisReport):
        self.report = report
        self.prompt = ""
        self.system_prompt = ""

    def call_sync(self, prompt, response_model, system_prompt=None):
        self.prompt = prompt
        self.system_prompt = system_prompt
        return self.report


def _build_db() -> _FakeDb:
    """端到端链路的数据批次：图谱岗位 → 三源(语义+关键词) → 历史诊断。

    retrieve_context 的检索顺序（无技能路）：_verified_positions（1 批）
    → _occupations 内部 search_authoritative（语义 1 批 + ILIKE 1 批）
    → _diagnoses（1 批）。
    """
    occupations = _tri_source_occupations()
    return _FakeDb(
        [  # 图谱已验证岗位定义（discovery_candidates emerging）
            DiscoveryCandidate(
                position_name=_POSITION,
                state="emerging",
                definition_draft="负责大模型应用、智能体与 RAG 系统的设计、开发与落地。",
            ),
        ],
        occupations,  # pgvector 语义路
        occupations,  # 关键词路（ILIKE）
        [  # 历史诊断报告
            DiagnosisReportRecord(
                match_id="m-ai-001",
                position_name=_POSITION,
                report={
                    "overall_summary": "总体匹配度中等，核心差距在生成式 AI 工程化。",
                    "top_gaps": [
                        {"skill": "大模型微调", "advice": "补齐 LoRA 微调与评测实践"}
                    ],
                },
            ),
        ],
    )


class TestTriSourceRetrieval:
    def test_position_hits_all_three_sources(self):
        """职业名「人工智能工程师」从三源 occupations 均产生检索命中。"""
        async def _run():
            db = _build_db()
            chunks = await retrieve_context(
                _POSITION, db, neo4j=None, embedder=_FakeEmbedder()
            )
            occ_ids = {
                c.evidence_id for c in chunks if c.source == "occupation"
            }
            # 三源 evidence_id 齐全（O*NET / 人社部 / LinkedIn）
            assert {"occupation:15-1252.00", "occupation:2-02-38-01", "occupation:LI-0001"} <= occ_ids
            # 三源定义内容完整进入上下文
            texts = [c.content for c in chunks if c.source == "occupation"]
            assert any("Software Developers" in t for t in texts)
            assert any("人工智能工程技术人员" in t for t in texts)
            assert any("AI Engineer" in t for t in texts)

        asyncio.run(_run())

    def test_position_and_diagnosis_sources_present(self):
        """图谱已验证岗位定义与历史诊断同样进入上下文（evidence_id 可追溯）。"""
        async def _run():
            db = _build_db()
            chunks = await retrieve_context(
                _POSITION, db, neo4j=None, embedder=_FakeEmbedder()
            )
            ids = {c.evidence_id for c in chunks}
            assert "position:人工智能工程师" in ids
            assert "diagnosis:m-ai-001" in ids

        asyncio.run(_run())


class TestDiagnosisGenerationE2E:
    def test_rag_context_injected_into_prompt(self):
        """检索结果注入诊断 prompt 的【图谱参考上下文】，附 evidence_id。"""
        async def _run():
            db = _build_db()
            chunks = await retrieve_context(
                _POSITION, db, neo4j=None, embedder=_FakeEmbedder()
            )
            rag_chunks = [c.__dict__ for c in chunks]

            report = DiagnosisReport(
                overall_summary="总体匹配度中等",
                radar_analysis="必备偏弱、加分尚可",
                top_gaps=[],
                path_analysis="",
                recommendations=[],
            )
            fake = _FakeLLM(report)
            generate_diagnosis(_diagnosis_data(), llm=fake, rag_chunks=rag_chunks)

            assert "【图谱参考上下文】" in fake.prompt
            # 三源定义文本 + 历史诊断内容都在 prompt 中
            assert "从事人工智能相关算法" in fake.prompt  # hrss 定义
            assert "AI Engineer" in fake.prompt  # linkedin 定义
            assert "Software Developers" in fake.prompt  # onet 定义
            assert "总体匹配度中等，核心差距在生成式 AI 工程化" in fake.prompt  # 历史诊断
            # 每条上下文附 evidence_id 供追溯
            assert "evidence_id: occupation:2-02-38-01" in fake.prompt
            assert "evidence_id: diagnosis:m-ai-001" in fake.prompt

        asyncio.run(_run())

    def test_evidence_id_grounded_in_retrieval_context(self):
        """§6.4 生成约束：LLM 引用的 evidence_id 必须来自检索上下文（可追溯闭环）。"""
        async def _run():
            db = _build_db()
            chunks = await retrieve_context(
                _POSITION, db, neo4j=None, embedder=_FakeEmbedder()
            )
            rag_chunks = [c.__dict__ for c in chunks]
            available_ids = {c["evidence_id"] for c in rag_chunks}

            # 模拟 LLM 从上下文选取证据生成差距建议（evidence_id 取自上下文）
            report = DiagnosisReport(
                overall_summary="总体匹配度中等",
                radar_analysis="",
                top_gaps=[
                    GapAdvice(
                        skill="大模型微调",
                        advice="参考人社部数字技术职业定义，补齐大模型微调与评测实践",
                        evidence_id="occupation:2-02-38-01",
                    ),
                ],
                path_analysis="",
                recommendations=["系统学习大模型微调"],
            )
            out = generate_diagnosis(_diagnosis_data(), llm=_FakeLLM(report), rag_chunks=rag_chunks)

            # LLM 引用的证据必须真实存在于检索上下文（拦截虚构引用）
            assert out.top_gaps[0].evidence_id in available_ids
            assert out.top_gaps[0].evidence_id == "occupation:2-02-38-01"

        asyncio.run(_run())
