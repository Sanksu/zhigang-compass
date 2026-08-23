"""SimHash 语义去重单元测试（设计文档 §4.2 去重）。

覆盖指纹计算、汉明距离、近似判定、批量相似对查找。
去重准确率目标 ≥ 95%（设计文档指标）。
"""

from app.services.data_quality.simhash import (
    DEFAULT_HAMMING_THRESHOLD,
    SimHashIndex,
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


class TestSimHashIndex:
    """P12: 增量式 SimHash 近邻索引（流式去重性能优化）。"""

    def _cluster_records(self):
        desc = ("负责公司核心业务系统后端开发，使用 Python 技术栈，"
                "参与高并发分布式系统设计与实现，熟悉 MySQL Redis 缓存 消息队列 微服务架构 容器化部署")
        return [
            ("jd1", simhash64("Python 后端开发工程师 " + desc)),
            ("jd2", simhash64("Python后端开发工程师 " + desc)),  # 仅空格差异 → 重复
            ("jd3", simhash64("前端 React 开发工程师 负责前端组件库设计与实现")),
        ]

    def test_incremental_scan_finds_all_batch_pairs(self):
        """增量逐条入索引的近邻检索结果 == 全量 find_similar_pairs 的重复对。"""
        records = self._cluster_records()
        index = SimHashIndex()
        incremental: set[tuple[str, str]] = set()
        for rid, fp in records:
            for cid in index.find_near(rid, fp):
                incremental.add(tuple(sorted((rid, cid))))  # 归一化顺序（方向无关）
            index.add(rid, fp)
        batch = set(tuple(sorted(p)) for p in find_similar_pairs(records))
        assert incremental == batch
        assert ("jd1", "jd2") in incremental

    def test_find_near_excludes_self(self):
        index = SimHashIndex()
        rid, fp = "jd1", simhash64("Python 后端开发工程师 分布式 高并发")
        index.add(rid, fp)
        assert index.find_near(rid, fp) == []  # 不把自身当作重复候选
        assert len(index) == 1

    def test_find_near_respects_threshold(self):
        index = SimHashIndex()
        index.add("a", simhash64("Python 后端开发工程师 微服务架构"))
        assert index.find_near("b", simhash64("Python后端开发工程师 微服务架构"))
        # 明显不同文本（无共享块/汉明距超阈值）不应命中
        assert index.find_near("c", simhash64("园林景观设计")) == []

    def test_from_items_roundtrip_restores_index(self):
        """持久化导出→恢复后近邻检索结果一致（Redis 重启恢复路径）。"""
        records = self._cluster_records()
        index = SimHashIndex.from_items(records)
        restored = SimHashIndex.from_items(index.items())
        for rid, fp in records:
            assert set(restored.find_near(rid, fp)) == set(index.find_near(rid, fp))
        assert len(restored) == len(records)

    def test_empty_index_no_near(self):
        assert SimHashIndex().find_near("jd1", simhash64("任意文本")) == []

    def test_invalid_threshold_rejected(self):
        """阈值 ≥ 块宽时抽屉原理不成立，构造应报错（防静默退化）。"""
        try:
            SimHashIndex(threshold=16)
        except ValueError:
            return
        raise AssertionError("threshold>=block_bits 应拒绝构造")
