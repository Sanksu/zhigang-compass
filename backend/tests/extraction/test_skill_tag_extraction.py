"""技能标签提取全链路验证测试（G-04 技能标签提取详细验证用例）。

与单测的关系：
- test_jd_extractor / test_resume_extractor：Mock LLM 验证降级路径与后处理
- test_post_processor / test_dictionary：词表与清洗单元
- 本文件：从**真实文本**出发，验证"文本 → 抽取 → 后处理 → 技能标签"端到端产出，
  并锁定技能标签口径（规范化 / 去重 / 过滤）不与评估基线（run_baseline.keyword_match）漂移。

覆盖点（对应设计文档 §5.2 / §8.2-8.3 / §13.3）：
1. JD 规则兜底：真实中文 JD → 白名单技能命中 + requirements 双写 + method=rule
2. JD LLM 路径：越界技能（软素质词/行业词/泛词碎片）被词典后过滤
3. 技能标签口径：别名归一 → 后缀清洗 → 白名单保护 → 单字符碎片剔除
4. 与评估基线一致性：canonical_skill_name 与 keyword_match 同口径
5. 简历抽取：规则兜底 + LLM 空结果回退 + 软技能并入
"""

from unittest.mock import patch

from app.services.extraction.dictionary import SKILL_STOPWORDS
from app.services.extraction.jd_extractor import JDExtractor
from app.services.extraction.llm_provider import LLMConfigurationError, LLMExtractionError
from app.services.extraction.post_processor import canonical_skill_name, post_process
from app.services.extraction.schemas import (
    JDExtractionResult,
    SkillExtracted,
)
from app.services.resume.extractor import ResumeExtractor
from app.services.resume.schemas import ResumeExtractionResult, ResumeSkill
from tests.evaluate.run_baseline import _norm_skill, keyword_match

# 一段真实感的中文 JD（技能齐全 + 软素质词 + 行业词，检验过滤）
_REAL_JD = """Python 后端开发工程师

岗位职责：
1. 负责核心交易系统的后端服务开发，使用 Python、MySQL、Redis 构建高可用接口；
2. 基于 Docker 容器化部署，参与 Kubernetes 集群运维；
3. 与产品团队协作，跟进需求分析。

任职要求：
1. 本科及以上学历，计算机相关专业；
2. 熟悉 Python、MySQL、Redis，了解 Docker 与 Kubernetes；
3. 吃苦耐劳，有责任心，具备良好的团队协作能力；
4. 有金融行业经验者优先。
"""


class TestJDRuleFallbackEndToEnd:
    """真实 JD 文本 → 规则兜底（无 LLM）→ 技能标签端到端。"""

    def test_real_jd_extracts_whitelist_skills(self):
        with patch(
            "app.services.extraction.jd_extractor.LLMProviderChain",
            side_effect=LLMConfigurationError("配置缺失"),
        ):
            extractor = JDExtractor()
        out = extractor.extract(_REAL_JD)
        names = {s.name for s in out.skills}
        # 白名单技能全部命中（真实文本含完整词）
        assert {"Python", "MySQL", "Redis", "Docker", "Kubernetes"}.issubset(names)
        # 软素质/行业词不入技能标签
        assert "吃苦耐劳" not in names
        assert "金融" not in names
        # requirements 与 skills 双写一致
        assert {r.skill_name for r in out.requirements} == names
        # P6：规则兜底全标 nice，不污染 must 判定
        assert all(r.necessity == "nice" for r in out.requirements)
        # 岗位名取首行短标题
        assert out.position_name == "Python 后端开发工程师"
        assert out.method == "rule"

    def test_rule_fallback_no_skill_text(self):
        with patch(
            "app.services.extraction.jd_extractor.LLMProviderChain",
            side_effect=LLMConfigurationError("配置缺失"),
        ):
            extractor = JDExtractor()
        jd = "岗位名称\n负责日常事务性工作，保证流程顺畅推进，按时交付。"
        out = extractor.extract(jd)
        assert out.skills == []
        assert out.requirements == []

    def test_rule_fallback_short_text_empty(self):
        with patch(
            "app.services.extraction.jd_extractor.LLMProviderChain",
            side_effect=LLMConfigurationError("配置缺失"),
        ):
            extractor = JDExtractor()
        assert extractor.extract("后端开发").skills == []
        assert extractor.extract("").skills == []


class _FakeLLM:
    """固定返回抽取结果的假 LLM 链。"""

    def __init__(self, result: JDExtractionResult):
        self.result = result
        self.calls = 0

    def extract_structured(self, prompt, response_model, **kwargs):
        self.calls += 1
        return self.result


class TestJDLLMPathFiltering:
    """LLM 返回越界技能 → 词典后过滤（真实链路 post_process）。"""

    def _post(self, result: JDExtractionResult) -> JDExtractionResult:
        return JDExtractor(llm=_FakeLLM(result)).extract(_REAL_JD)

    def test_soft_quality_noise_filtered(self):
        # LLM 把招聘软素质误抽为技术技能 → 词典后过滤剔除
        out = self._post(JDExtractionResult(
            position_name="",
            skills=[
                SkillExtracted(name="Python"),
                SkillExtracted(name="吃苦耐劳"),
                SkillExtracted(name="有责任心"),
            ],
        ))
        assert [s.name for s in out.skills] == ["Python"]

    def test_stopword_industry_filtered(self):
        # 行业/业务词黑名单（LLM 在正文缺失时误抽）→ 剔除
        out = self._post(JDExtractionResult(
            position_name="",
            skills=[
                SkillExtracted(name="MySQL"),
                SkillExtracted(name="金融"),
                SkillExtracted(name="保险"),
            ],
        ))
        assert [s.name for s in out.skills] == ["MySQL"]

    def test_generic_fragment_filtered(self):
        # 泛词碎片（P1-2 评估报告：JD 高频泛词被误抽）→ 剔除
        out = self._post(JDExtractionResult(
            position_name="",
            skills=[
                SkillExtracted(name="Redis"),
                SkillExtracted(name="系统"),
                SkillExtracted(name="前端"),
                SkillExtracted(name="数据处理"),
            ],
        ))
        assert [s.name for s in out.skills] == ["Redis"]

    def test_suffix_cleaned_and_deduped(self):
        # 后缀清洗 + 别名归一 + 大小写去重（保留首次）
        # （直调 post_process：词面守卫属 extract 层，另有专项测试）
        out = post_process(JDExtractionResult(
            position_name="",
            skills=[
                SkillExtracted(name="Docker 技术"),
                SkillExtracted(name="docker 技术"),
                SkillExtracted(name="golang"),
                SkillExtracted(name="SpringBoot框架"),
            ],
        ))
        assert [s.name for s in out.skills] == ["Docker", "Go", "Spring Boot"]

    def test_soft_skills_whitelist_only(self):
        # soft_skills 仅保留岗位本体 20 项白名单，越界输出剔除
        out = self._post(JDExtractionResult(
            position_name="",
            soft_skills=["团队协作", "沟通能力", "体力好", "团队协作"],
        ))
        assert out.soft_skills == ["团队协作", "沟通能力"]


class TestSkillTagCanonicalConsistency:
    """技能标签口径：与评估基线 keyword_match 保持一致，防漂移。"""

    def test_canonical_matches_eval_norm(self):
        # 抽取链路 canonical_skill_name.lower() == 评估基线 _norm_skill
        for raw in ["Golang", "vue", "spring", "Docker 技术", "Vue3框架", "mybatis-plus框架"]:
            assert canonical_skill_name(raw).lower() == _norm_skill(raw), raw

    def test_keyword_match_detects_regression(self):
        # 若抽取链路规范化与黄金集对齐，keyword_match 应能正确判定 tp/fp/fn
        pred = ["Python", "Docker 技术"]
        gold = ["Python", "Docker", "Kubernetes"]
        tp, fp, fn = keyword_match(pred, gold)
        assert tp == 2
        assert fp == 0
        assert fn == 1

    def test_stopwords_are_noise_for_skills(self):
        # 黑名单词是噪音词的判定源头（评估与抽取共用），不得作为有效技能标签
        for w in ["金融", "系统", "前端", "软件", "运营"]:
            assert w in SKILL_STOPWORDS
            # 规范化后不会变成其他有效技能（保持原词或清洗为空）
            normed = _norm_skill(w)
            assert normed in ("", w.lower()), (w, normed)


class TestResumeSkillExtractionEndToEnd:
    """真实简历文本 → 简历抽取（规则兜底 / LLM 空结果回退 / 软技能并入）。"""

    _RESUME = """[NAME]
[EMAIL] · [PHONE] · 上海市
教育背景：南京大学，软件工程，本科，2020.9 — 2024.6
专业技能：
精通 Python 与 MySQL，熟悉 Redis、Docker，掌握 Kubernetes；
了解 Golang。
项目经历：
电商平台重构项目，负责后端开发，使用 Python、MySQL。
工作经历：某科技公司，后端工程师，3 年经验。
"""

    def test_rule_fallback_extracts_skills_with_proficiency(self):
        with patch(
            "app.services.resume.extractor.LLMProviderChain",
            side_effect=LLMConfigurationError("配置缺失"),
        ):
            extractor = ResumeExtractor()
        out = extractor.extract(self._RESUME)
        names = {s.name for s in out.skills}
        assert {"Python", "MySQL", "Redis", "Docker", "Kubernetes", "Go"}.issubset(names)
        # 熟练度：精通→3，熟悉→2，了解→1（窗口取技能名之前最近句读）
        by_name = {s.name: s.proficiency for s in out.skills}
        assert by_name.get("Python") == 3
        assert by_name.get("Redis") == 2
        assert by_name.get("Go") == 1
        # 教育/年限
        assert out.education_level == "本科"
        assert out.total_years == 3.0

    def test_llm_empty_result_falls_back_to_rules(self):
        # LLM 返回空对象（provider 解析失败实测场景）→ 规则兜底
        class _EmptyLLM:
            def __init__(self):
                self.calls = 0

            def extract_structured(self, prompt, response_model, **kwargs):
                self.calls += 1
                return ResumeExtractionResult()

        fake = _EmptyLLM()
        extractor = ResumeExtractor(llm=fake)
        out = extractor.extract(self._RESUME)
        assert {"Python", "MySQL", "Redis"}.issubset({s.name for s in out.skills})
        assert fake.calls == 1

    def test_llm_failure_falls_back_to_rules(self):
        class _FailingLLM:
            def extract_structured(self, prompt, response_model, **kwargs):
                raise LLMExtractionError("provider 全挂")

        extractor = ResumeExtractor(llm=_FailingLLM())
        out = extractor.extract(self._RESUME)
        assert "Python" in {s.name for s in out.skills}

    def test_stopwords_filtered_from_resume_skills(self):
        # 简历技能同样剔除行业/业务词（与 JD 同口径）
        fake = _FakeResumeLLM(ResumeExtractionResult(skills=[ResumeSkill(name="Python"), ResumeSkill(name="金融")]))
        extractor = ResumeExtractor(llm=fake)
        out = extractor.extract(self._RESUME)
        assert [s.name for s in out.skills] == ["Python"]

    def test_soft_skills_merged_with_low_confidence(self):
        # LLM 推断软技能并入技能列表，标记 low_confidence（匹配时降权 ×0.5）
        fake = _FakeResumeLLM(ResumeExtractionResult(
            skills=[ResumeSkill(name="Python", proficiency=3)],
            soft_skills=["团队协作", "沟通能力", "体力好"],
        ))
        extractor = ResumeExtractor(llm=fake)
        out = extractor.extract(self._RESUME)
        by_name = {s.name: s for s in out.skills}
        assert "Python" in by_name and by_name["Python"].low_confidence is False
        assert "团队协作" in by_name and by_name["团队协作"].low_confidence is True
        assert "沟通能力" in by_name
        # 非白名单软技能（体力好）不入技能列表
        assert "体力好" not in by_name


class _FakeResumeLLM:
    """固定返回结果的假简历 LLM 链。"""

    def __init__(self, result: ResumeExtractionResult):
        self.result = result

    def extract_structured(self, prompt, response_model, **kwargs):
        return self.result
