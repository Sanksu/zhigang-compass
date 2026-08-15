"""课程时长解析 + 课程名门控单元测试（AL-M4-03，设计文档 §4.6）。

门控测试用假语义器注入预设相似度（不加载 SBERT），覆盖 08-15 灰色带
质量门控实证阈值：sim ∈ [0.5, 0.62) 带内仅保留质量分 ≥0.62 的课程
（Office→Excel 0.553/q0.658 保留；多线程→高级英语 0.558/q低 拦截）。
"""

from app.services.learning_path.courses import (
    _COURSE_TITLE_SIM_THRESHOLD,
    _filter_by_title_similarity,
    parse_duration_hours,
)


class _FakeSemantic:
    """按 (a, b) 对返回预设相似度的假语义器，warm 为无操作。"""

    def __init__(self, sims: dict[tuple[str, str], float]):
        self.sims = sims

    def warm(self, names):
        pass

    def similarity(self, a: str, b: str) -> float:
        return self.sims.get((a, b), 0.0)


def _row(course_id: str, title: str, source: str = "edx") -> dict:
    return {"id": course_id, "name": title, "source": source, "source_id": course_id}


class TestTitleSimilarityGate:
    """P1-3 课程名语义门控 + 08-15 灰色带质量门控（PR #192 治理）。"""

    def test_below_threshold_filtered(self):
        """sim < 0.5 过滤（实证误配 Genomic Data Science 0.01-0.25 档）。"""
        rows = [_row("c1", "Genomic Data Science")]
        semantic = _FakeSemantic({("Unix Shell", "Genomic Data Science"): 0.2})
        kept = _filter_by_title_similarity(rows, "Unix Shell", semantic, _COURSE_TITLE_SIM_THRESHOLD)
        assert kept == []

    def test_above_threshold_kept(self):
        """sim ≥ 0.62 直接保留（实证合理课程 Python for Everybody 0.796 档）。"""
        rows = [_row("c1", "Python for Everybody")]
        semantic = _FakeSemantic({("Python", "Python for Everybody"): 0.796})
        kept = _filter_by_title_similarity(rows, "Python", semantic, _COURSE_TITLE_SIM_THRESHOLD)
        assert len(kept) == 1

    def test_gray_zone_high_quality_kept(self):
        """灰带 [0.5, 0.62) + 质量分 ≥0.62 保留（实证 Office→Excel 0.553/q0.658）。"""
        rows = [_row("c1", "Excel Skills for Business")]
        semantic = _FakeSemantic({("Office", "Excel Skills for Business"): 0.553})
        quality = {("edx", "c1"): {"quality_score": 0.658}}
        kept = _filter_by_title_similarity(
            rows, "Office", semantic, _COURSE_TITLE_SIM_THRESHOLD, quality)
        assert len(kept) == 1

    def test_gray_zone_low_quality_filtered(self):
        """灰带 + 质量分 <0.62 拦截（实证误配多线程→高级英语 0.558/q低）。"""
        rows = [_row("c1", "高级英语")]
        semantic = _FakeSemantic({("多线程", "高级英语"): 0.558})
        quality = {("edx", "c1"): {"quality_score": 0.41}}
        kept = _filter_by_title_similarity(
            rows, "多线程", semantic, _COURSE_TITLE_SIM_THRESHOLD, quality)
        assert kept == []

    def test_gray_zone_missing_quality_filtered(self):
        """灰带 + 质量分缺失（未评估课程）拦截——宁缺毋滥。"""
        rows = [_row("c1", "简明世界史")]
        semantic = _FakeSemantic({("Qlik", "简明世界史"): 0.551})
        kept = _filter_by_title_similarity(
            rows, "Qlik", semantic, _COURSE_TITLE_SIM_THRESHOLD, quality_map={})
        assert kept == []

    def test_gray_zone_upper_bound_excluded(self):
        """sim = 0.62 不在灰带内（[0.5, 0.62) 右开），低质量分也不拦截。"""
        rows = [_row("c1", "Some Course")]
        semantic = _FakeSemantic({("技能", "Some Course"): 0.62})
        quality = {("edx", "c1"): {"quality_score": 0.3}}
        kept = _filter_by_title_similarity(
            rows, "技能", semantic, _COURSE_TITLE_SIM_THRESHOLD, quality)
        assert len(kept) == 1

    def test_lexical_hit_exempted(self):
        """词面命中豁免：课程名包含技能名（缩写场景 AWS 0.472 虚低）直接保留。"""
        rows = [_row("c1", "AWS Cloud Technical Essentials")]
        semantic = _FakeSemantic({("AWS", "AWS Cloud Technical Essentials"): 0.472})
        kept = _filter_by_title_similarity(rows, "AWS", semantic, _COURSE_TITLE_SIM_THRESHOLD)
        assert len(kept) == 1

    def test_hint_hit_low_quality_filtered(self):
        """弱词面命中（_EN_SKILL_HINTS 关键词）豁免 sim 阈值但质量底线仍生效。

        08-15 审查：'微服务'↔标题含 microservice 的课程 sim 可能 < 0.5
        （中英跨语言短词虚低），词面直通会绕过灰色带质量门控放大误配——
        低质量课程即使命中关键词也拦截（宁缺毋滥）。
        """
        rows = [_row("c1", "Microservices with .NET")]
        semantic = _FakeSemantic({("微服务", "Microservices with .NET"): 0.42})
        quality = {("edx", "c1"): {"quality_score": 0.4}}
        kept = _filter_by_title_similarity(
            rows, "微服务", semantic, _COURSE_TITLE_SIM_THRESHOLD, quality)
        assert kept == []

    def test_hint_hit_high_quality_kept(self):
        """弱词面命中 + 质量 ≥0.62 → 保留（词面相关 + 质量达标双条件）。"""
        rows = [_row("c1", "Microservices with .NET")]
        semantic = _FakeSemantic({("微服务", "Microservices with .NET"): 0.42})
        quality = {("edx", "c1"): {"quality_score": 0.71}}
        kept = _filter_by_title_similarity(
            rows, "微服务", semantic, _COURSE_TITLE_SIM_THRESHOLD, quality)
        assert len(kept) == 1

    def test_hint_hit_without_quality_map_kept(self):
        """弱词面 + quality_map 未提供 → 通过（与 semantic=None 降级行为一致）。"""
        rows = [_row("c1", "Microservices with .NET")]
        semantic = _FakeSemantic({("微服务", "Microservices with .NET"): 0.42})
        kept = _filter_by_title_similarity(rows, "微服务", semantic, _COURSE_TITLE_SIM_THRESHOLD)
        assert len(kept) == 1

    def test_semantic_none_keeps_all(self):
        """semantic=None（纯规则链路）不过滤——语义不可用降级行为。"""
        rows = [_row("c1", "任意课程")]
        kept = _filter_by_title_similarity(rows, "技能", None, _COURSE_TITLE_SIM_THRESHOLD)
        assert len(kept) == 1


class TestParseDurationHours:
    def test_chinese_weeks(self):
        assert parse_duration_hours("10 周") == 400.0

    def test_english_weeks(self):
        assert parse_duration_hours("6 weeks") == 240.0

    def test_chinese_months(self):
        assert parse_duration_hours("2 个月") == 320.0

    def test_days(self):
        assert parse_duration_hours("3 days") == 24.0

    def test_hours(self):
        assert parse_duration_hours("5 hours") == 5.0

    def test_years(self):
        assert parse_duration_hours("1 年") == 1920.0

    def test_decimal(self):
        assert parse_duration_hours("1.5 周") == 60.0

    def test_missing_returns_none(self):
        assert parse_duration_hours(None) is None
        assert parse_duration_hours("") is None

    def test_no_number_returns_none(self):
        assert parse_duration_hours("入门课程") is None

    def test_unknown_unit_returns_none(self):
        assert parse_duration_hours("10 学分") is None

    def test_surrounding_text_tolerated(self):
        assert parse_duration_hours("约 4 周左右") == 160.0
