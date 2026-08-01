"""SimHash 语义去重单元测试（设计文档 §4.2 去重）。

覆盖指纹计算、汉明距离、近似判定、批量相似对查找。
去重准确率目标 ≥ 95%（设计文档指标）。
"""

from app.services.data_quality.simhash import (
    DEFAULT_HAMMING_THRESHOLD,
    find_similar_pairs,
    hamming_distance,
    is_duplicate,
    simhash64,
)


class TestSimhash64:
    def test_identical_text_identical_fingerprint(self):
        assert simhash64("Python 后端开发工程师") == simhash64("Python 后端开发工程师")

    def test_empty_text_returns_zero(self):
        assert simhash64("") == 0
        assert simhash64("   ") == 0

    def test_tokenize_ignores_case(self):
        assert simhash64("Python java") == simhash64("python JAVA")

    def test_same_tokens_different_order_same_fingerprint(self):
        """SimHash 对词序不敏感（跨平台标题词序差异不影响判定）。"""
        assert simhash64("后端开发工程师 Python") == simhash64("Python 后端开发工程师")


class TestHammingDistance:
    def test_known_distances(self):
        assert hamming_distance(0b0000, 0b1111) == 4
        assert hamming_distance(0b1010, 0b1010) == 0
        assert hamming_distance(0b1010, 0b0000) == 2

    def test_same_fingerprint_zero_distance(self):
        a = simhash64("Python 后端开发工程师")
        assert hamming_distance(a, a) == 0


class TestIsDuplicate:
    def test_identical_is_duplicate(self):
        a = simhash64("Python 后端开发工程师")
        assert is_duplicate(a, a) is True

    def test_near_text_is_duplicate(self):
        """跨平台同岗位（空格/后缀格式差异）判定为重复。"""
        desc = ("负责公司核心业务系统后端开发，使用 Python 技术栈，"
                "参与高并发分布式系统设计与实现，熟悉 MySQL Redis 缓存 消息队列 微服务架构 容器化部署")
        a = simhash64("Python 后端开发工程师 " + desc)
        b = simhash64("Python后端开发工程师 " + desc)  # 仅空格差异
        assert is_duplicate(a, b) is True

    def test_unrelated_text_not_duplicate(self):
        a = simhash64("Python 后端开发工程师")
        b = simhash64("前端网页设计排版")
        assert is_duplicate(a, b) is False

    def test_threshold_boundary(self):
        desc = ("负责公司核心业务系统后端开发，使用 Python 技术栈，"
                "参与高并发分布式系统设计与实现，熟悉 MySQL Redis 缓存 消息队列 微服务架构 容器化部署")
        a = simhash64("Python 后端开发工程师 " + desc)
        b = simhash64("Python 后端开发工程师（北京） " + desc)  # 追加城市后缀
        distance = hamming_distance(a, b)
        assert distance <= 3  # 设计阈值边界内
        assert is_duplicate(a, b, threshold=distance) is True
        assert is_duplicate(a, b, threshold=distance - 1) is False


class TestFindSimilarPairs:
    def test_detects_similar_pairs(self):
        desc = ("负责公司核心业务系统后端开发，使用 Python 技术栈，"
                "参与高并发分布式系统设计与实现，熟悉 MySQL Redis 缓存 消息队列 微服务架构 容器化部署")
        records = [
            ("jd1", simhash64("Python 后端开发工程师 " + desc)),
            ("jd2", simhash64("Python后端开发工程师 " + desc)),  # 仅空格差异 → 重复
            ("jd3", simhash64("前端 React 开发工程师 负责前端组件库设计与实现")),
        ]
        pairs = find_similar_pairs(records)
        assert ("jd1", "jd2") in pairs
        assert len(pairs) == 1

    def test_no_pairs_for_disjoint_texts(self):
        records = [
            ("a", simhash64("Java 微服务架构")),
            ("b", simhash64("园林景观设计")),
            ("c", simhash64("会计税务申报")),
        ]
        assert find_similar_pairs(records) == []

    def test_empty_records(self):
        assert find_similar_pairs([]) == []


class TestAccuracy:
    """近似去重准确率场景：相似组全召回，无关组无误报。"""

    def test_dedupe_accuracy_on_similar_group(self):
        # 同一岗位跨平台变体（同 description，标题仅格式差异）：均应判定为互相重复
        desc = ("负责公司核心业务系统后端开发，使用 Python 技术栈，"
                "参与高并发分布式系统设计与实现，熟悉 MySQL Redis 缓存 消息队列 微服务架构 容器化部署")
        group = [
            "Python 后端开发工程师 " + desc,
            "Python后端开发工程师 " + desc,
            "Python 后端开发工程师（北京） " + desc,
            "Python 后端开发工程师(资深) " + desc,
        ]
        fingerprints = [simhash64(t) for t in group]
        base = fingerprints[0]
        # 组内任意两两均判定重复（召回率 100%）
        assert all(is_duplicate(base, f) for f in fingerprints)

    def test_no_false_positive_across_distinct_roles(self):
        roles = [
            "Python 后端开发工程师",
            "前端 React 开发工程师",
            "数据分析师 机器学习",
            "运维工程师 Kubernetes",
        ]
        fingerprints = [simhash64(r) for r in roles]
        pairs = find_similar_pairs(
            [(f"r{i}", fp) for i, fp in enumerate(fingerprints)]
        )
        assert pairs == []

    def test_threshold_constant_within_design(self):
        assert DEFAULT_HAMMING_THRESHOLD == 3
