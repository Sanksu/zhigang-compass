"""课程时长解析 + 课程名门控单元测试（AL-M4-03，设计文档 §4.6）。

门控测试用假语义器注入预设相似度（不加载 SBERT），覆盖 08-15 灰色带
质量门控实证阈值与 08-28 方案 A 全域质量底线：非词面自证的语义命中一律
要求质量分 ≥0.62（并发↔运筹学 0.806 等带外虚高误配族实证）。
"""

from app.services.learning_path.courses import (
    _COURSE_TITLE_SIM_THRESHOLD,
    _filter_by_title_similarity,
    _lexical_hit,
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

    def test_band_upper_low_quality_filtered(self):
        """sim = 0.62 带外低质量分拦截（08-28 方案 A：质量底线全域生效）。

        旧口径此配对直通（[0.5,0.62) 带外无质量要求）——正是运筹学误配族
        （sim 0.62-0.81、q≈0.52）的放行通道，方案 A 后与带内同判。
        """
        rows = [_row("c1", "Some Course")]
        semantic = _FakeSemantic({("技能", "Some Course"): 0.62})
        quality = {("edx", "c1"): {"quality_score": 0.3}}
        kept = _filter_by_title_similarity(
            rows, "技能", semantic, _COURSE_TITLE_SIM_THRESHOLD, quality)
        assert kept == []

    def test_lexical_hit_exempted(self):
        """词面命中豁免：课程名包含技能名（缩写场景 AWS 0.472 虚低）直接保留。"""
        rows = [_row("c1", "AWS Cloud Technical Essentials")]
        semantic = _FakeSemantic({("AWS", "AWS Cloud Technical Essentials"): 0.472})
        kept = _filter_by_title_similarity(rows, "AWS", semantic, _COURSE_TITLE_SIM_THRESHOLD)
        assert len(kept) == 1

    def test_lexical_hit_token_suffix(self):
        """08-16 增强：带后缀/多词技能名整串不命中时按 token 匹配
        （Express.js 课程名仅 "…Node.js and Express"、RESTful API vs "…RESTful技术"）。"""
        assert _lexical_hit("Express.js", "Developing Back-End Apps with Node.js and Express")
        assert _lexical_hit("RESTful API", "Web服务与RESTful技术")
        assert _lexical_hit("Node.js", "Node.js应用开发")
        # 整串命中优先（Claude Code 课程名含完整词）
        assert _lexical_hit("Claude Code", "Claude Code Fundamentals")

    def test_lexical_hit_generic_token_blocked(self):
        """08-16：泛化 token（code/app/api/web 等）不构成词面信号
        （Claude Code 的 "code" ∈ "No-Code…" 实证误配）；短词豁免保持。"""
        assert not _lexical_hit("Claude Code", "No-Code Machine Learning Using Amazon AWS")

    def test_lexical_hit_word_boundary(self):
        """08-29 词边界化：子串命中复合词内部不算词面自证。

        226 移动端全栈实证："iOS" ⊂ 西语 "negocios"（Excel para los negocios）
        曾词面直通 3 门 Excel 课；"sql" ⊂ "nosql" 同族。中英混排边界不受影响
        （CJK 相邻视为边界）。"""
        # 复合词内部子串不再命中
        assert not _lexical_hit("iOS", "Excel para los negocios nivel intermedio")
        assert not _lexical_hit("SQL", "NoSQL, Big Data and Spark Fundamentals")
        assert not _lexical_hit("Java", "JavaScript Frameworks Fundamentals")
        # 正常词边界命中不受影响
        assert _lexical_hit("iOS", "iOS 开发实战")
        assert _lexical_hit("iOS", "IOS Development for Beginners")
        assert _lexical_hit("SQL", "Databases and SQL for Data Science")
        assert not _lexical_hit("Go", "Introduction to Go Programming")

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


class TestGlobalQualityFloor:
    """08-28 方案 A：非词面自证的语义命中一律过质量底线（≥0.62）。

    实证背景（226 全栈工程师 pos_3052 全技能扫描）：13 个中文抽象 nice 技能
    经课程池兜底拉入运筹学/土力学/高级英语/世界史等垃圾课，sim 0.62-0.81
    全部落在原灰色带 [0.5,0.62) 之外直通；垃圾课质量分 0.5065-0.5718。
    """

    def test_yunchou_high_sim_low_quality_filtered(self):
        """实证误配：并发↔运筹学 sim=0.806、q=0.519 → 拦截（原口径直通）。"""
        rows = [_row("c1", "运筹学", source="icourse163")]
        semantic = _FakeSemantic({("并发", "运筹学"): 0.8055})
        quality = {("icourse163", "c1"): {"quality_score": 0.5193}}
        kept = _filter_by_title_similarity(
            rows, "并发", semantic, _COURSE_TITLE_SIM_THRESHOLD, quality)
        assert kept == []

    def test_high_sim_missing_quality_filtered(self):
        """带外高 sim + 质量分缺失（未评估）拦截——宁缺毋滥推广到全域。"""
        rows = [_row("c1", "信息描述", source="icourse163")]
        semantic = _FakeSemantic({("事务", "信息描述"): 0.70})
        kept = _filter_by_title_similarity(
            rows, "事务", semantic, _COURSE_TITLE_SIM_THRESHOLD, quality_map={})
        assert kept == []

    def test_high_sim_high_quality_kept(self):
        """带外高 sim + 质量 ≥0.62 保留（语义相关且质量达标的正常命中）。"""
        rows = [_row("c1", "Excel Skills for Business")]
        semantic = _FakeSemantic({("Office", "Excel Skills for Business"): 0.70})
        quality = {("edx", "c1"): {"quality_score": 0.658}}
        kept = _filter_by_title_similarity(
            rows, "Office", semantic, _COURSE_TITLE_SIM_THRESHOLD, quality)
        assert len(kept) == 1

    def test_lexical_hit_exempt_from_quality_floor(self):
        """词面命中相关性自证，无质量分也保留（Docker↔Docker容器技术）。"""
        rows = [_row("c1", "Docker容器技术", source="icourse163")]
        semantic = _FakeSemantic({("Docker", "Docker容器技术"): 0.48})
        kept = _filter_by_title_similarity(
            rows, "Docker", semantic, _COURSE_TITLE_SIM_THRESHOLD, quality_map={})
        assert len(kept) == 1

    def test_quality_floor_neutral_without_quality_map(self):
        """quality_map=None（调用方无质量数据）保持纯规则链路不过滤。"""
        rows = [_row("c1", "运筹学", source="icourse163")]
        semantic = _FakeSemantic({("并发", "运筹学"): 0.8055})
        kept = _filter_by_title_similarity(
            rows, "并发", semantic, _COURSE_TITLE_SIM_THRESHOLD)
        assert len(kept) == 1


class TestCrossLangNoOverlapGate:
    """08-22 跨语言无词面交集门控（Airflow↔航空气象学 / PostgreSQL↔MySQL 误配族）。"""

    def test_airflow_aviation_meteorology_filtered(self):
        """实证误配：Airflow↔航空气象学 sim=0.6632，超灰带上限但 <0.75 → 拦截。

        旧门控下该配对直通（灰色带 [0.5,0.62) 之外无防线），质量分 0.55 也不拦。
        """
        rows = [_row("c1", "航空气象学", source="icourse163")]
        semantic = _FakeSemantic({("Airflow", "航空气象学"): 0.6632})
        quality = {("icourse163", "c1"): {"quality_score": 0.55}}
        kept = _filter_by_title_similarity(
            rows, "Airflow", semantic, _COURSE_TITLE_SIM_THRESHOLD, quality)
        assert kept == []

    def test_postgresql_mysql_course_filtered(self):
        """实证误配：PostgreSQL↔MySQL 课 sim=0.548 → 拦截（原 P1-1 可救案例口径废除）。"""
        rows = [_row("c1", "新编数据库技术—MySQL", source="icourse163")]
        semantic = _FakeSemantic({("PostgreSQL", "新编数据库技术—MySQL"): 0.548})
        quality = {("icourse163", "c1"): {"quality_score": 0.635}}
        kept = _filter_by_title_similarity(
            rows, "PostgreSQL", semantic, _COURSE_TITLE_SIM_THRESHOLD, quality)
        assert kept == []

    def test_cross_lang_high_sim_kept(self):
        """跨语言无交集但 sim ≥0.75 → 保留（语义强相关的翻译配对）。"""
        rows = [_row("c1", "机器学习")]
        semantic = _FakeSemantic({("Machine Learning", "机器学习"): 0.939})
        kept = _filter_by_title_similarity(
            rows, "Machine Learning", semantic, _COURSE_TITLE_SIM_THRESHOLD)
        assert len(kept) == 1

    def test_lexical_overlap_exempt_from_cross_lang_gate(self):
        """词面交集豁免：Docker↔Docker容器技术（token 命中）不受 0.75 加严约束。"""
        rows = [_row("c1", "Docker容器技术")]
        # sim 虚低（跨语言短词）但词面命中 → 直接保留
        semantic = _FakeSemantic({("Docker", "Docker容器技术"): 0.48})
        kept = _filter_by_title_similarity(
            rows, "Docker", semantic, _COURSE_TITLE_SIM_THRESHOLD)
        assert len(kept) == 1

    def test_ascii_title_not_affected(self):
        """英文课程标题（无中文）不走跨语言门控，维持原 0.5/灰带逻辑。"""
        rows = [_row("c1", "Databases and SQL for Data Science")]
        semantic = _FakeSemantic({("PostgreSQL", "Databases and SQL for Data Science"): 0.55})
        kept = _filter_by_title_similarity(
            rows, "PostgreSQL", semantic, _COURSE_TITLE_SIM_THRESHOLD)
        assert len(kept) == 1

    def test_cjk_skill_not_affected(self):
        """技能名含中文（非纯 ASCII）不走跨语言门控（微服务等 hint 链路保持）。"""
        rows = [_row("c1", "高级英语")]
        semantic = _FakeSemantic({("多线程", "高级英语"): 0.558})
        quality = {("edx", "c1"): {"quality_score": 0.41}}
        # 与既有灰带测试同参数：走灰带质量门控拦截（而非跨语言 0.75 直拦）
        kept = _filter_by_title_similarity(
            rows, "多线程", semantic, _COURSE_TITLE_SIM_THRESHOLD, quality)
        assert kept == []


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
