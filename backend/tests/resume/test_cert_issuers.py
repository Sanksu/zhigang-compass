"""证书颁发机构映射单元测试（M5 测试补充）。

覆盖 cert_issuers.py 全部纯函数：
- _norm：归一化（小写/标点转空格/合并空白/保留中文）
- canonical_issuer：颁发机构简写 → 全称规范化
- issuer_for：证书名 → 颁发机构（精确+前缀，最长优先）
- CERT_ISSUER_MAP / ISSUER_CANONICAL_MAP 数据完整性校验

设计原则：
1. 精确匹配优先，前缀匹配次之
2. 最长 key 优先，避免短前缀误吞（azure 优先于 az）
3. 未命中返回空串（不错误补全）
4. 中文无前缀空格边界，ASCII 前缀需空格边界
"""

from app.services.resume.cert_issuers import (
    CERT_ISSUER_MAP,
    ISSUER_CANONICAL_MAP,
    _norm,
    canonical_issuer,
    issuer_for,
)


class TestNorm:
    """_norm 归一化函数。"""

    def test_lowercase(self):
        """ASCII 字母转小写。"""
        assert _norm("AWS") == "aws"
        assert _norm("CISSP") == "cissp"

    def test_punctuation_to_space(self):
        """各种标点转为空格。"""
        assert _norm("AWS/GCP") == "aws gcp"
        assert _norm("AWS-Certified") == "aws certified"
        assert _norm("Cisco(CCNA)") == "cisco ccna"
        assert _norm("Google.Cloud") == "google cloud"
        assert _norm("OCI: Oracle") == "oci oracle"
        assert _norm("OCI_Oracle") == "oci oracle"
        assert _norm("OCI–Oracle") == "oci oracle"  # en dash
        assert _norm("OCI—Oracle") == "oci oracle"  # em dash

    def test_whitespace_collapsed(self):
        """多空白合并为单空格。"""
        assert _norm("AWS   Certified") == "aws certified"
        assert _norm("  AWS  ") == "aws"

    def test_chinese_preserved(self):
        """中文字符保留不变。"""
        assert _norm("华为认证") == "华为认证"
        assert _norm("阿里云ACP") == "阿里云acp"

    def test_empty_string(self):
        """空串 → 空串。"""
        assert _norm("") == ""
        assert _norm("   ") == ""

    def test_mixed_chinese_english(self):
        """中英文混合归一化。"""
        assert _norm("AWS Certified 解决方案架构师") == "aws certified 解决方案架构师"

    def test_square_brackets(self):
        """方括号转空格。"""
        assert _norm("[ISC]²") == "isc ²"

    def test_curly_braces(self):
        """花括号转空格。"""
        assert _norm("{test}") == "test"


class TestCanonicalIssuer:
    """canonical_issuer：颁发机构简写规范化。"""

    def test_known_abbreviation_expands(self):
        """已知简写 → 全称。"""
        assert canonical_issuer("aws") == "Amazon Web Services (AWS)"
        assert canonical_issuer("AWS") == "Amazon Web Services (AWS)"
        assert canonical_issuer("cisco") == "Cisco"
        assert canonical_issuer("pmi") == "Project Management Institute (PMI)"

    def test_full_name_passthrough(self):
        """已是全称 → 原样返回。"""
        assert canonical_issuer("Cisco") == "Cisco"
        assert canonical_issuer("随便一个机构") == "随便一个机构"

    def test_case_insensitive(self):
        """大小写不敏感。"""
        assert canonical_issuer("CISCO") == "Cisco"
        assert canonical_issuer("Aws") == "Amazon Web Services (AWS)"

    def test_empty_string_passthrough(self):
        """空串 → 原空串。"""
        assert canonical_issuer("") == ""

    def test_chinese_abbreviation(self):
        """中文简写规范化。"""
        assert canonical_issuer("软考") == "中国计算机技术职业资格（人社部/工信部）"
        assert canonical_issuer("阿里云") == "阿里云"

    def test_unknown_issuer_passthrough(self):
        """未知机构 → 原样返回。"""
        assert canonical_issuer("some random org") == "some random org"


class TestIssuerForExactMatch:
    """issuer_for：精确匹配。"""

    def test_exact_match_aws(self):
        """精确匹配：aws → AWS。"""
        assert issuer_for("aws") == "Amazon Web Services (AWS)"

    def test_exact_match_case_insensitive(self):
        """大小写不敏感（归一化后比较）。"""
        assert issuer_for("CISSP") == "(ISC)²"
        assert issuer_for("PMP") == "Project Management Institute (PMI)"

    def test_exact_match_chinese(self):
        """中文精确匹配。"""
        assert issuer_for("软考") == "中国计算机技术职业资格（人社部/工信部）"
        assert issuer_for("华为") == "华为"


class TestIssuerForPrefixMatch:
    """issuer_for：前缀匹配。"""

    def test_ascii_prefix_with_space_boundary(self):
        """ASCII 前缀需空格边界。"""
        # "aws " 前缀命中
        assert issuer_for("AWS Certified Solutions Architect") == "Amazon Web Services (AWS)"

    def test_ascii_prefix_no_boundary_not_matched(self):
        """ASCII 前缀无空格边界 → 不命中（防误吞，如 csp 不吞 cspm）。"""
        # "csp" 是 key，但 "cspm" 不以 "csp " 开头 → 不命中
        assert issuer_for("cspm certification") == ""

    def test_chinese_prefix_no_boundary(self):
        """中文前缀无需空格边界（中文无分词）。"""
        assert issuer_for("软考高级") == "中国计算机技术职业资格（人社部/工信部）"
        assert issuer_for("阿里云ACP") == "阿里云"

    def test_longest_prefix_wins(self):
        """多 key 命中时取最长（短前缀不误吞）。"""
        # "microsoft azure" 比 "microsoft" 长，验证不同长度 key 都映射到同一 issuer
        result = issuer_for("Microsoft Azure Fundamentals")
        assert result == "Microsoft"  # 两者都映射到 Microsoft
        # 验证标点归一化后前缀命中：az-900 → az 900 → "az " 前缀命中
        assert issuer_for("az-900") == "Microsoft"  # - 被归一化为空格 → az 前缀命中

    def test_oracle_database_vs_oracle_cloud(self):
        """Oracle 不同前缀对应不同 issuer。"""
        assert issuer_for("Oracle Database 19c") == "Oracle"  # oracle database → Oracle
        assert issuer_for("Oracle Cloud Architect") == "Oracle Cloud Infrastructure"  # oracle cloud → OCI


class TestIssuerForEdgeCases:
    """issuer_for：边界与回退。"""

    def test_empty_string_returns_empty(self):
        """空串 → 空串。"""
        assert issuer_for("") == ""
        assert issuer_for("   ") == ""

    def test_unknown_cert_returns_empty(self):
        """未知证书 → 空串（不错误补全）。"""
        assert issuer_for("随便一个认证") == ""
        assert issuer_for("some random cert") == ""

    def test_too_generic_not_in_map(self):
        """过泛名称（如 security、sql）未建条目 → 空串。"""
        assert issuer_for("security") == ""
        assert issuer_for("sql") == ""

    def test_cert_with_punctuation_normalized(self):
        """带标点的证书名先归一化再匹配。"""
        assert issuer_for("AWS-Certified-Developer") == "Amazon Web Services (AWS)"
        assert issuer_for("Cisco(CCNA)") == "Cisco"  # 精确匹配 "cisco"

    def test_comptia_variants(self):
        """CompTIA 多种写法都能命中。"""
        assert issuer_for("CompTIA Security+") == "CompTIA"
        assert issuer_for("comptia a+") == "CompTIA"

    def test_google_certified(self):
        """Google Certified 前缀。"""
        assert issuer_for("Google Certified Professional Cloud Architect") == "Google Cloud"


class TestCertIssuerMapIntegrity:
    """CERT_ISSUER_MAP 数据完整性校验。"""

    def test_map_not_empty(self):
        """映射表非空。"""
        assert len(CERT_ISSUER_MAP) > 100

    def test_all_keys_lowercase_normalized(self):
        """所有 key 都是小写归一化形式（与 _norm 输出一致）。"""
        for key in CERT_ISSUER_MAP:
            assert key == _norm(key), f"key 未归一化: {key!r}"

    def test_all_values_non_empty_strings(self):
        """所有 value 都是非空字符串。"""
        for key, val in CERT_ISSUER_MAP.items():
            assert isinstance(val, str), f"{key} 的 value 不是字符串"
            assert len(val) > 0, f"{key} 的 value 为空"

    def test_no_duplicate_values_for_same_issuer_family(self):
        """同一机构的多种写法映射到相同 value。"""
        # AWS 家族
        assert CERT_ISSUER_MAP["aws"] == CERT_ISSUER_MAP["amazon web services"]
        # 微软家族
        assert CERT_ISSUER_MAP["azure"] == CERT_ISSUER_MAP["microsoft"]
        assert CERT_ISSUER_MAP["microsoft azure"] == CERT_ISSUER_MAP["microsoft"]
        # Google 家族
        assert CERT_ISSUER_MAP["gcp"] == CERT_ISSUER_MAP["google cloud"]


class TestIssuerCanonicalMapIntegrity:
    """ISSUER_CANONICAL_MAP 数据完整性校验。"""

    def test_map_not_empty(self):
        """规范化映射非空。"""
        assert len(ISSUER_CANONICAL_MAP) > 30

    def test_all_keys_normalized(self):
        """所有 key 都是归一化形式。"""
        for key in ISSUER_CANONICAL_MAP:
            assert key == _norm(key), f"key 未归一化: {key!r}"

    def test_all_values_non_empty(self):
        """所有 value 非空。"""
        for key, val in ISSUER_CANONICAL_MAP.items():
            assert isinstance(val, str) and len(val) > 0, f"{key} value 异常"

    def test_canonical_issuer_idempotent(self):
        """规范化后再规范化结果不变（幂等）。"""
        for key in ISSUER_CANONICAL_MAP:
            full = canonical_issuer(key)
            assert canonical_issuer(full) == full, f"{key} 规范化不幂等"
