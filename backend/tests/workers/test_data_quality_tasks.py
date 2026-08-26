"""worker 时滞/通胀检测辅助纯函数单元测试（设计文档 §4.7/4.8）。

只测 tasks.py 中不依赖数据库的纯函数（数据组装逻辑），
DB 交互（validate_temporal / detect_inflation 任务本体）在真实环境验证。
"""

from datetime import date
from types import SimpleNamespace

from app.workers.tasks import (
    _build_jd_text,
    _experience_years,
    _extraction_of,
    _publish_date,
    _skill_first_seen_days,
    _skills_of,
)


class TestBuildJdText:
    def test_body_fields_present_concatenates(self):
        snap = {"title": "Python开发", "description": "负责后端开发", "requirements": "熟悉Django"}
        text = _build_jd_text(snap, "RAW")
        assert "负责后端开发" in text
        assert "熟悉Django" in text
        assert "RAW" not in text

    def test_body_missing_falls_back_to_raw_text(self):
        # 黄金集等数据正文字段缺失，正文只存在 raw_text
        snap = {"title": "Python开发"}
        assert _build_jd_text(snap, "RAW_FULL_TEXT") == "RAW_FULL_TEXT"

    def test_body_blank_falls_back_to_raw_text(self):
        snap = {"title": "Python开发", "description": "  ", "requirements": ""}
        assert _build_jd_text(snap, "RAW") == "RAW"

    def test_education_hint_no_double_when_joined_fields_include_edu(self):
        # snapshot.education 已随 _JD_TEXT_FIELDS 拼入正文（含学历词）→ 不追加，防重复投喂
        snap = {"title": "数据工程师", "description": "负责数仓建设", "education": "本科"}
        assert "\n【教育要求】" not in _build_jd_text(snap, "RAW")

    def test_education_hint_appended_on_raw_fallback(self):
        # body 字段缺失回退 raw_text、正文缺学历词但列表页学历非空（黄金集形态）
        # → 追加独立【教育要求】行（#537 学历弱维的真实缺口路径）
        snap = {"education": "硕士"}
        assert "\n【教育要求】硕士" in _build_jd_text(snap, "负责算法研发")

    def test_education_hint_not_appended_when_raw_has_keyword(self):
        # raw_text 已含学历信号 → 不追加
        snap = {"education": "硕士"}
        assert "\n【教育要求】" not in _build_jd_text(snap, "需要硕士及以上学历")

    def test_education_hint_not_appended_when_edu_blank(self):
        # 快照无学历 → 原样返回（与历史一致）
        snap = {"title": "数据工程师", "description": "负责数仓建设", "education": ""}
        assert "\n【教育要求】" not in _build_jd_text(snap, "RAW")


class TestExtractionOf:
    def test_returns_extraction_dict(self):
        row = SimpleNamespace(snapshot={"extraction": {"position_name": "Python 工程师"}})
        assert _extraction_of(row) == {"position_name": "Python 工程师"}

    def test_missing_returns_none(self):
        assert _extraction_of(SimpleNamespace(snapshot={})) is None
        assert _extraction_of(SimpleNamespace(snapshot=None)) is None


class TestSkillsOf:
    def test_requirements_preferred(self):
        ext = {
            "skills": [{"name": "Java"}],
            "requirements": [{"skill_name": "Python"}, {"skill_name": "MySQL"}],
        }
        assert _skills_of(ext) == ["Python", "MySQL"]

    def test_fallback_to_skills(self):
        ext = {"skills": [{"name": "Go"}, {"name": "Docker"}], "requirements": []}
        assert _skills_of(ext) == ["Go", "Docker"]

    def test_empty(self):
        assert _skills_of({}) == []


class TestPublishDate:
    def test_post_date_preferred(self):
        snapshot = {"post_date": "2026-07-30"}
        assert _publish_date(snapshot, "2026-07-01T08:00:00+08:00") == date(2026, 7, 30)

    def test_fallback_to_crawled_at(self):
        snapshot = {}
        assert _publish_date(snapshot, "2026-07-01T08:00:00+08:00") == date(2026, 7, 1)

    def test_slash_format(self):
        assert _publish_date({"post_date": "2026/07/15"}, "") == date(2026, 7, 15)

    def test_space_datetime_format(self):
        # 回归：post_date 为 "YYYY-MM-DD HH:MM:SS"（智联等源，占库内 46%）
        # 此前 _publish_date 缺此格式导致时滞检测被误标 no_skills_or_publish_date 跳过
        assert _publish_date({"post_date": "2026-08-06 17:34:16"}, "") == date(2026, 8, 6)

    def test_iso_t_format(self):
        assert _publish_date({"post_date": "2026-08-06T14:29:08Z"}, "") == date(2026, 8, 6)

    def test_unparseable_returns_none(self):
        assert _publish_date({"post_date": "新鲜出炉"}, "not-a-date") is None
        assert _publish_date({}, "") is None


class TestSkillFirstSeenDays:
    def test_age_from_earliest_appearance(self):
        today = date(2026, 8, 1)
        group = [
            (1, date(2026, 7, 1), ["Python"]),   # Python 首见 31 天前
            (2, date(2026, 7, 20), ["Python", "MySQL"]),  # MySQL 首见 12 天前
        ]
        # 当前 JD 只含 Python → 首见 31 天
        assert _skill_first_seen_days(group, ["Python"], today) == [31]
        # 当前 JD 含 Python + MySQL → [31, 12]
        assert _skill_first_seen_days(group, ["Python", "MySQL"], today) == [31, 12]

    def test_includes_current_jd_own_first_appearance(self):
        today = date(2026, 8, 1)
        group = [(1, date(2026, 8, 1), ["Rust"])]  # 仅当前 JD 含 Rust
        assert _skill_first_seen_days(group, ["Rust"], today) == [0]

    def test_skill_without_history_excluded(self):
        today = date(2026, 8, 1)
        group = [(1, date(2026, 7, 1), ["Python"])]
        # Rust 在同岗位无记录 → 不计入（不武断判定）
        assert _skill_first_seen_days(group, ["Python", "Rust"], today) == [31]

    def test_never_negative(self):
        today = date(2026, 8, 1)
        group = [(1, date(2026, 8, 10), ["Python"])]  # 未来日期（脏数据）
        assert _skill_first_seen_days(group, ["Python"], today) == [0]

    def test_graph_first_seen_priority(self):
        today = date(2026, 8, 1)
        group = [(1, date(2026, 7, 30), ["Python"])]  # jd_raw 首见 2 天前
        # 图谱 first_seen 更早（全局首入图）→ 优先图谱，不取 jd_raw
        graph = {"Python": date(2026, 6, 1)}
        assert _skill_first_seen_days(group, ["Python"], today, graph) == [61]

    def test_graph_missing_falls_back_to_jd_raw(self):
        today = date(2026, 8, 1)
        group = [(1, date(2026, 7, 1), ["Python"])]
        # 图谱无该技能（存量节点无 first_seen）→ 回退同岗位 jd_raw 推算
        graph = {"MySQL": date(2026, 7, 20)}
        assert _skill_first_seen_days(group, ["Python", "MySQL"], today, graph) == [31, 12]


class TestExperienceYears:
    def test_range_text(self):
        assert _experience_years({"experience": "3-5年"}) == 3

    def test_min_text(self):
        assert _experience_years({"experience": "5年以上"}) == 5

    def test_plain_years(self):
        assert _experience_years({"experience": "2年"}) == 2

    def test_unparseable_returns_none(self):
        assert _experience_years({"experience": "经验不限"}) is None
        assert _experience_years({}) is None
