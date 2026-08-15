"""课程技能抽取门控单测（T-05，2026-08-15）。

覆盖：
- filter_skill_tags：爬虫/LLM 原始标签 → canonical 归一化 + 停用词/白名单门控
- extract_course_skills：LLM 不可用/失败降级、非法输出过滤
"""

from app.services.extraction.course_skills import (
    CourseSkillResult,
    build_prompt,
    extract_course_skills,
    filter_skill_tags,
)


class TestFilterSkillTags:
    def test_normalize_and_dedup(self):
        out = filter_skill_tags(["Python", "python ", "机器学习", "机器学习"])
        assert out == ["Python", "机器学习"]

    def test_stopword_filtered(self):
        # "日志"/"英语" 为 SKILL_STOPWORDS（P5/P6 批次），"审批" 为业务词
        out = filter_skill_tags(["Python", "日志", "英语", "审批"])
        assert out == ["Python"]

    def test_empty_and_invalid(self):
        assert filter_skill_tags([]) == []
        assert filter_skill_tags(["", "  ", None]) == []
        # dict 形态（import_course 兼容 ["name"] 结构）
        assert filter_skill_tags([{"name": "Docker"}]) == ["Docker"]

    def test_whitelist_kept(self):
        # 白名单词（日志分析）不受停用词影响——精确停用词不伤复合词
        out = filter_skill_tags(["日志分析", "Docker"])
        assert "日志分析" in out


class TestExtractCourseSkills:
    def test_llm_none_returns_empty(self):
        assert extract_course_skills(None, "Python 入门", "从零学习 Python") == []

    def test_llm_failure_degrades(self):
        class _Boom:
            def extract_structured(self, *a, **k):
                raise RuntimeError("provider 不可用")

        assert extract_course_skills(_Boom(), "课程", "描述") == []

    def test_invalid_output_filtered(self):
        class _FakeLLM:
            def extract_structured(self, prompt, response_model, **k):
                return CourseSkillResult(
                    skills=["Python", "日志", "  ", "监控", "Docker", "英语四六级"]
                )

        # 日志/监控/英语四六级 为停用词（P5/P6 批次），空白丢弃
        out = extract_course_skills(_FakeLLM(), "课程", "描述")
        assert out == ["Python", "Docker"]

    def test_build_prompt_includes_title(self):
        p = build_prompt("Python 入门", "从零学习 Python 编程")
        assert "Python 入门" in p and "从零学习" in p
