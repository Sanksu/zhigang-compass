

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


class TestBoundaryRulesCalibration:
    """校准 r5：边界口径规则（四轮复测 16 类语义边界错误的政策显式化）。"""

    def test_boundary_rules_cover_confusion_clusters(self):
        from app.services.extraction.skill_category_review import _BOUNDARY_RULES

        # r4 实证错误簇的关键裁决必须在场
        for phrase in ("数据质量", "Dubbo", "Ceph", "Microsoft 365", "毫米波雷达",
                       "计算机网络", "ES5", "异步编程", "线性代数", "RTMP"):
            assert phrase in _BOUNDARY_RULES, f"边界规则缺 {phrase}"

    def test_prompt_includes_boundary_rules(self):
        from app.services.extraction.skill_category_review import _TASK_TEMPLATE

        assert "{boundary_rules}" in _TASK_TEMPLATE
        assert "边界口径（权威分类政策" in _TASK_TEMPLATE
