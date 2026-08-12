"""证书 issuer 静态映射表与补全逻辑测试。"""

import pytest

from app.services.resume.cert_issuers import issuer_for
from app.services.resume.extractor import ResumeExtractor
from app.services.resume.schemas import ResumeCertification, ResumeExtractionResult


class TestIssuerFor:
    def test_exact_overseas(self):
        assert issuer_for("PMP") == "Project Management Institute (PMI)"
        assert issuer_for("CISSP") == "(ISC)²"
        assert issuer_for("CCNA") == "Cisco"

    def test_prefix_matches_full_name(self):
        # "AWS Certified Solutions Architect" 以 aws 前缀命中
        assert issuer_for("AWS Certified Solutions Architect") == "Amazon Web Services (AWS)"
        assert issuer_for("Azure Administrator Associate") == "Microsoft"
        assert issuer_for("CompTIA Security+") == "CompTIA"

    def test_code_forms(self):
        assert issuer_for("AZ-204") == "Microsoft"
        assert issuer_for("SY0-701") == ""  # 纯考试号无映射，不错误补全

    def test_longest_prefix_wins(self):
        # "azure" 优先于 "az"
        assert issuer_for("Azure") == "Microsoft"
        # "oracle cloud" 优先于 "oracle"
        assert issuer_for("Oracle Cloud Security Professional") == "Oracle Cloud Infrastructure"

    def test_chinese(self):
        assert issuer_for("软考高级项目管理师") == "中国计算机技术职业资格（人社部/工信部）"
        assert issuer_for("华为 HCIP") == "华为"
        assert issuer_for("等保三级") == "公安部（等级保护）"

    def test_generic_not_found(self):
        # 泛化名称/无歧义来源的不建条目，返回空串
        assert issuer_for("云认证") == ""
        assert issuer_for("AI认证") == ""
        assert issuer_for("") == ""


class TestFillCertIssuers:
    def test_fills_empty_issuer(self):
        result = ResumeExtractionResult(
            certifications=[ResumeCertification(name="PMP"), ResumeCertification(name="CCIE")]
        )
        out = ResumeExtractor._fill_cert_issuers(result)
        assert out.certifications[0].issuer == "Project Management Institute (PMI)"
        assert out.certifications[1].issuer == "Cisco"

    def test_keeps_llm_issuer(self):
        # LLM 已给出 issuer 时词表不覆盖
        result = ResumeExtractionResult(
            certifications=[ResumeCertification(name="PMP", issuer="PMI（文本写明）")]
        )
        out = ResumeExtractor._fill_cert_issuers(result)
        assert out.certifications[0].issuer == "PMI（文本写明）"

    def test_unmapped_stays_empty(self):
        result = ResumeExtractionResult(
            certifications=[ResumeCertification(name="未知证书XYZ")]
        )
        out = ResumeExtractor._fill_cert_issuers(result)
        assert out.certifications[0].issuer == ""

    def test_empty_certifications_noop(self):
        result = ResumeExtractionResult(certifications=[])
        out = ResumeExtractor._fill_cert_issuers(result)
        assert out.certifications == []

    def test_canonicalizes_llm_short_form(self):
        # LLM 抽出简写"软考"→ 规范化为全称
        result = ResumeExtractionResult(
            certifications=[ResumeCertification(name="信息系统项目管理师", issuer="软考")]
        )
        out = ResumeExtractor._fill_cert_issuers(result)
        assert out.certifications[0].issuer == "中国计算机技术职业资格（人社部/工信部）"

    def test_canonicalizes_pmi(self):
        result = ResumeExtractionResult(
            certifications=[ResumeCertification(name="PMP", issuer="PMI")]
        )
        out = ResumeExtractor._fill_cert_issuers(result)
        assert out.certifications[0].issuer == "Project Management Institute (PMI)"

    def test_keeps_contextual_issuer(self):
        # 含上下文的 issuer（"PMI（中国分部）"）不属于简写映射，保留原值不误规范化
        result = ResumeExtractionResult(
            certifications=[ResumeCertification(name="PMP", issuer="PMI（中国分部）")]
        )
        out = ResumeExtractor._fill_cert_issuers(result)
        assert out.certifications[0].issuer == "PMI（中国分部）"

    def test_keeps_unmapped_llm_issuer(self):
        # 非简写、词表未收录的 issuer 保持原值
        result = ResumeExtractionResult(
            certifications=[ResumeCertification(name="GrokNet", issuer="GrokNet 官方")]
        )
        out = ResumeExtractor._fill_cert_issuers(result)
        assert out.certifications[0].issuer == "GrokNet 官方"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
