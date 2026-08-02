"""课程时长解析单元测试（AL-M4-03，设计文档 §4.6）。"""

from app.services.learning_path.courses import parse_duration_hours


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
