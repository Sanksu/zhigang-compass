"""岗位名语义对齐单元测试（BE-位置对齐）。

覆盖 PositionAligner 的匹配策略：
1. 规则已归并（命中关键词族）→ 直接返回标准名，语义不参与（防误并）
2. 图谱已有岗位名 → 直接复用（不触发语义）
3. 语义命中 ≥ 阈值 → 替换为图谱已有岗位名
4. 语义未命中 → 保留规则归一化结果
5. 模型不可用 / 图谱加载失败 → 降级纯规则，不抛异常

测试用例中的"未命中族"岗位名均经 normalize_position_name 实测确认
（如 "LLM应用" 不命中任何关键词族、无后缀可剥，"DevOps" 同），
避免把规则已归并的名字误当作语义匹配输入。
"""

from app.services.extraction.position_align import (
    _SEMANTIC_THRESHOLD,
    PositionAligner,
)


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


def _make(known: list[str], sim: dict | None = None) -> tuple[PositionAligner, _FakeEmbedder]:
    embedder = _FakeEmbedder(sim)
    aligner = PositionAligner(embedder=embedder, name_source=lambda: list(known))
    return aligner, embedder


class TestRuleMergedSkipsSemantic:
    def test_keyword_family_returns_standard_without_semantic(self):
        """命中关键词族（"数据分析"→"数据分析师"）→ 语义不触发。"""
        aligner, embedder = _make(known=["数据工程师"])
        assert aligner.align("数据分析师") == "数据分析师"
        assert embedder.warmed == []  # 语义未参与

    def test_family_standard_never_overridden(self):
        """族标准名即便图谱没有也不做语义替换（规则权威，防误并）。"""
        aligner, embedder = _make(known=["数据工程师"], sim={("测试工程师", "数据工程师"): 0.99})
        assert aligner.align("测试工程师") == "测试工程师"
        assert embedder.warmed == []


class TestKnownNameReuse:
    def test_existing_graph_position_reused_without_semantic(self):
        """图谱已有该岗位名 → 直接复用，不触发语义。"""
        aligner, embedder = _make(known=["LLM应用"])
        assert aligner.align("LLM应用") == "LLM应用"
        assert embedder.warmed == []


class TestSemanticMatch:
    def test_hit_above_threshold_replaces(self):
        """语义相似度 ≥ 阈值 → 替换为图谱已有岗位名。"""
        sim = {("LLM应用", "算法工程师"): 0.95}
        aligner, _ = _make(known=["算法工程师"], sim=sim)
        assert aligner.align("LLM应用") == "算法工程师"

    def test_devops_aligned_to_operations(self):
        """规则未归并的运维相近岗位 → 语义匹配图谱"运维工程师"（历史 B 类问题自动化）。

        原用例输入 "DevOps" 已被 P1 新增 DevOps 族规则拦截（→ DevOps工程师），
        改用同样未命中族的 "系统管理员" 验证语义兜底链路。
        """
        sim = {("系统管理员", "运维工程师"): 0.95}
        aligner, _ = _make(known=["运维工程师"], sim=sim)
        assert aligner.align("系统管理员") == "运维工程师"

    def test_devops_family_merged_skips_semantic(self):
        """P1 新增 DevOps 族后，"DevOps" 规则命中 → DevOps工程师，语义不参与。"""
        aligner, embedder = _make(known=["运维工程师"], sim={("DevOps", "运维工程师"): 0.95})
        assert aligner.align("DevOps") == "DevOps工程师"
        assert embedder.warmed == []

    def test_hit_below_threshold_keeps(self):
        """语义相似度 < 阈值 → 保留规则归一化结果。"""
        sim = {("LLM应用", "数据工程师"): 0.82}
        aligner, _ = _make(known=["数据工程师"], sim=sim)
        assert aligner.align("LLM应用") == "LLM应用"

    def test_threshold_boundary_exact(self):
        """恰好等于阈值 → 命中。"""
        sim = {("LLM应用", "算法工程师"): _SEMANTIC_THRESHOLD}
        aligner, _ = _make(known=["算法工程师"], sim=sim)
        assert aligner.align("LLM应用") == "算法工程师"

    def test_many_aligns_all(self):
        """批量对齐：同批共享图谱岗位名加载。"""
        sim = {("LLM应用", "算法工程师"): 0.95}
        aligner, _ = _make(known=["算法工程师"], sim=sim)
        assert aligner.align_many(["算法工程师", "LLM应用"]) == ["算法工程师", "算法工程师"]


class TestDegradation:
    def test_embedder_unavailable_falls_back_to_rules(self):
        """SBERT 不可用 → 静默返回规则归一化结果，不抛异常。"""
        aligner, embedder = _make(known=["算法工程师"])
        embedder.broken = True
        assert aligner.align("LLM应用") == "LLM应用"

    def test_graph_unavailable_falls_back_to_rules(self):
        """图谱加载失败 → 仅规则归并，不抛异常。"""

        def _boom():
            raise RuntimeError("Neo4j 不可达")

        aligner, _ = _make(known=["算法工程师"])
        aligner._name_source = _boom
        assert aligner.align("LLM应用") == "LLM应用"

    def test_empty_position_name(self):
        """泛词/实习岗位 → 空串（不入图），与 normalize_position_name 一致。"""
        aligner, _ = _make(known=["Python开发工程师"])
        assert aligner.align("技术") == ""
        assert aligner.align("前端实习生") == ""
