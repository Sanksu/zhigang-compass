"""诊断报告生成器单元测试（设计文档 §9.5 节）。

覆盖：prompt 渲染（分数/差距/路径/证据）、Top-5 裁剪、空数据兜底、
LLM 异常向上传播（由 API 层转 503）。
"""

from app.services.diagnosis.generator import (
    _render_evidence,
    _render_gaps,
    _render_path,
    generate_diagnosis,
)
from app.services.diagnosis.schemas import DiagnosisReport
from app.services.extraction.llm_provider import LLMConfigurationError


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


def _sample_data() -> dict:
    return {
        "position_name": "高级数据分析师",
        "total_score": 0.62,
        "must_score": 0.5,
        "nice_score": 0.8,
        "exp_score": 0.75,
        "matched_must": ["SQL"],
        "missing_must": ["Python"],
        "gaps": [
            {"skill": "Python", "gap_type": "missing", "priority": "high"},
            {"skill": "机器学习", "gap_type": "weak", "priority": "medium"},
        ],
        "learning_path": [
            {
                "skill": "Python",
                "estimated_hours": 40,
                "courses": [{"title": "Python 基础", "platform": "MOOC"}],
            },
        ],
        "evidence_refs": [
            {"skill": "Python", "source": "JD#12", "url": "https://example.com/jd/12"},
        ],
    }


class TestRenderSections:
    def test_render_gaps_truncates_to_top5(self):
        gaps = [
            {"skill": f"s{i}", "gap_type": "missing", "priority": "high"}
            for i in range(7)
        ]
        lines = _render_gaps(gaps).splitlines()
        assert len(lines) == 5

    def test_render_path_lists_courses(self):
        text = _render_path(
            [
                {
                    "skill": "Python",
                    "estimated_hours": 40,
                    "courses": [{"title": "Python 基础"}, {"title": "NumPy"}],
                },
            ]
        )
        assert "Python" in text and "40" in text and "Python 基础" in text

    def test_render_evidence_prefers_url(self):
        text = _render_evidence(
            [{"skill": "Python", "source": "JD#12", "url": "https://example.com/jd/12"}]
        )
        assert "https://example.com/jd/12" in text

    def test_render_empty_sections_fallback(self):
        assert _render_gaps([]) == "无"
        assert _render_path([]) == "无"
        assert _render_evidence([]) == "无"


class TestGenerateDiagnosis:
    def test_prompt_contains_all_context(self):
        report = DiagnosisReport(
            overall_summary="总体匹配度中等",
            radar_analysis="必备偏弱、加分尚可",
            top_gaps=[],
            path_analysis="",
            recommendations=[],
        )
        fake = _FakeLLM(report)
        out = generate_diagnosis(_sample_data(), llm=fake)
        assert out.overall_summary == "总体匹配度中等"
        for fragment in [
            "高级数据分析师",
            "0.62",
            "SQL",
            "Python",
            "https://example.com/jd/12",
        ]:
            assert fragment in fake.prompt

    def test_empty_data_uses_fallbacks(self):
        report = DiagnosisReport(
            overall_summary="", radar_analysis="", top_gaps=[],
            path_analysis="", recommendations=[],
        )
        fake = _FakeLLM(report)
        out = generate_diagnosis({}, llm=fake)
        assert out.overall_summary == ""
        # matched/missing/gaps/path/evidence 均兜底为 "无"
        assert fake.prompt.count("无") >= 5

    def test_llm_configuration_error_propagates(self):
        class _BrokenLLM:
            def call_sync(self, prompt, response_model, system_prompt=None):
                raise LLMConfigurationError("未配置可用 provider")

        try:
            generate_diagnosis(_sample_data(), llm=_BrokenLLM())
            raise AssertionError("应抛出 LLMConfigurationError")
        except LLMConfigurationError:
            pass

    def test_rag_chunks_rendered_with_evidence_id(self):
        """RAG 图谱上下文渲染进 prompt，且附带 evidence_id 供追溯。"""
        report = DiagnosisReport(
            overall_summary="总体匹配度中等",
            radar_analysis="必备偏弱、加分尚可",
            top_gaps=[],
            path_analysis="",
            recommendations=[],
        )
        fake = _FakeLLM(report)
        rag_chunks = [
            {
                "content": "AI Agent 工程师：负责智能体应用设计开发",
                "evidence_id": "position:AI Agent 工程师",
                "source": "position_definition",
            },
            {
                "content": "岗位 AI 工程师 历史诊断：总体匹配度中等",
                "evidence_id": "diagnosis:abc-123",
                "source": "diagnosis",
            },
        ]
        out = generate_diagnosis(_sample_data(), llm=fake, rag_chunks=rag_chunks)
        assert out.overall_summary == "总体匹配度中等"
        assert "AI Agent 工程师：负责智能体应用设计开发" in fake.prompt
        assert "evidence_id: position:AI Agent 工程师" in fake.prompt
        assert "evidence_id: diagnosis:abc-123" in fake.prompt
        assert "【图谱参考上下文】" in fake.prompt

    def test_rag_chunks_empty_falls_back_to_wu(self):
        """无 RAG 上下文时图谱上下文区块渲染为「无」。"""
        report = DiagnosisReport(
            overall_summary="", radar_analysis="", top_gaps=[],
            path_analysis="", recommendations=[],
        )
        fake = _FakeLLM(report)
        generate_diagnosis(_sample_data(), llm=fake, rag_chunks=None)
        assert "【图谱参考上下文】\n无" in fake.prompt
