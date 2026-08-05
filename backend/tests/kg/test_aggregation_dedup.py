"""回归测试：SimHash 去重消费方（聚合层跳过 _duplicate_of 标记记录）。

原实现 pipelines 计算 snapshot._simhash 后无任何消费方，跨平台近似去重
（设计文档 §4.2，指标 ≥95%）未端到端落地。修复：dedup_simhash 任务标记
后入库记录 snapshot._duplicate_of，聚合层 build_aggregates 跳过重复记录。
"""

from app.services.kg.aggregation import build_aggregates


class _Row:
    """最小 JDRaw 行替身（build_aggregates 仅用 snapshot/source）。"""

    def __init__(self, snapshot, source="boss"):
        self.snapshot = snapshot
        self.source = source


def _jd(pos="Java开发工程师"):
    return {
        "extraction": {
            "position_name": pos,
            "requirements": [{"skill_name": "Java", "necessity": "must", "level": ""}],
        }
    }


def test_duplicate_marked_rows_skipped_from_aggregation():
    """被 dedup_simhash 标记 _duplicate_of 的后入库记录不参与聚合（不虚高频次）。"""
    rows = [
        _Row(_jd()),                                   # 先入库，保留
        _Row({**_jd(), "_duplicate_of": "1"}),         # 后入库，标记为重复 → 跳过
        _Row({**_jd(), "_duplicate_of": "1"}),
    ]
    agg = build_aggregates(rows)
    assert "Java开发工程师" in agg
    assert agg["Java开发工程师"].jd_count == 1  # 3 条中仅 1 条计入


def test_unmarked_rows_all_aggregated():
    """无重复标记时全部记录正常聚合。"""
    rows = [_Row(_jd()), _Row(_jd(), source="zhilian")]
    agg = build_aggregates(rows)
    assert agg["Java开发工程师"].jd_count == 2
