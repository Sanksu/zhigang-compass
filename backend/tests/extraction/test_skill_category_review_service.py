

class TestCategoryAnchors:
    """校准 r1：类目锚点（近邻易混口径显式给到 prompt）。"""

    def test_anchors_cover_all_categories(self):
        from app.services.extraction.skill_category_review import (
            KNOWN_CATEGORIES, category_anchors,
        )

        anchors = category_anchors()
        for category in KNOWN_CATEGORIES:
            assert category in anchors, f"锚点缺类：{category}"

    def test_prompt_contains_anchors_and_boundary_rule(self):
        from app.services.extraction.skill_category_review import classify_skill  # noqa: F401
        from app.services.extraction.skill_category_review import _TASK_TEMPLATE

        assert "{anchors}" in _TASK_TEMPLATE
        assert "SQL" in _TASK_TEMPLATE  # 易混边界口径（查询语言归编程语言）
