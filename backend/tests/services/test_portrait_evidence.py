"""画像条目证据回溯单测（纯函数部分；DB 过滤链路由集成测试覆盖）。

覆盖 portrait_evidence.py 的条目标签计算与行→条目映射：
- entry_label：salary（解析成功才落条目）/ experience（列表页年限文本）/
  education（抽取六维 level）/ 未知维度
- _item：snapshot 字段映射 + snippet 截断 + 缺省值兜底
- jd_detail：查看侧/管理侧共用字段映射 + experience_range 展示文本
"""

from types import SimpleNamespace

from app.services.graph.portrait_evidence import (
    _item,
    entry_label,
    jd_detail,
)


def _snapshot(**overrides) -> dict:
    snap = {
        "title": "Python 开发工程师",
        "company": "某科技",
        "location": "北京",
        "experience": "3-5年",
        "extraction": {
            "salary_range": "1-1.3万",
            "education": {"level": "本科"},
            "experience_range": {"min_years": 3, "max_years": 5},
        },
    }
    snap.update(overrides)
    return snap


def _row(snapshot: dict, raw_text: str = "岗位职责：负责后端服务开发与维护。", jd_id: int = 42):
    return SimpleNamespace(
        id=jd_id,
        snapshot=snapshot,
        raw_text=raw_text,
        source="zhilian",
        source_id="zhilian-42",
        source_url="https://example.com/jd/42",
        crawled_at="2026-08-28 10:00:00",
        is_desensitized=False,
    )


class TestEntryLabel:
    def test_salary_label_parsed(self):
        snap = _snapshot()
        assert entry_label(snap, "salary") == "1-1.3万"

    def test_salary_label_unparsed_text_excluded(self):
        """薪资原文无法解析 → 不落任何条目（与聚合口径一致）。"""
        snap = _snapshot(extraction={"salary_range": "面议"})
        assert entry_label(snap, "salary") == ""

    def test_experience_label_from_list_page_years(self):
        snap = _snapshot()
        assert entry_label(snap, "experience") == "3年以上"

    def test_experience_label_from_extraction_range(self):
        """经验标签来自 extraction.experience_range（权威抽取字段），
        即便列表页 experience 文本为空也以抽取为准（P0 口径）。"""
        snap = _snapshot(experience="")
        assert entry_label(snap, "experience") == "3年以上"

    def test_experience_label_missing_years(self):
        """抽取无经验年限 → 经验标签为空（不落条目）。"""
        snap = _snapshot(experience="", extraction={"education": {"level": "本科"}})
        assert entry_label(snap, "experience") == ""

    def test_education_label_from_extraction(self):
        snap = _snapshot()
        assert entry_label(snap, "education") == "本科"

    def test_education_label_empty_level(self):
        snap = _snapshot(extraction={"education": {"level": "  "}})
        assert entry_label(snap, "education") == ""

    def test_unknown_dimension_returns_empty(self):
        assert entry_label(_snapshot(), "skills") == ""


class TestItem:
    def test_item_fields_mapped(self):
        item = _item(_row(_snapshot()))
        assert item["jd_id"] == 42
        assert item["title"] == "Python 开发工程师"
        assert item["company"] == "某科技"
        assert item["source"] == "zhilian"
        assert item["source_url"] == "https://example.com/jd/42"
        assert item["crawled_at"] == "2026-08-28 10:00:00"
        assert item["salary_text"] == "1-1.3万"
        assert item["experience_label"] == "3年以上"
        assert item["education_level"] == "本科"

    def test_snippet_truncated_to_120(self):
        raw = "长" * 500
        item = _item(_row(_snapshot(), raw_text=raw))
        assert len(item["snippet"]) == 120
        assert set(item["snippet"]) == {"长"}

    def test_item_missing_optional_fields(self):
        row = _row({"extraction": {}}, raw_text="")
        row.source_url = ""
        item = _item(row)
        assert item["title"] == ""
        assert item["company"] == ""
        assert item["salary_text"] == ""
        assert item["experience_label"] == ""
        assert item["education_level"] == ""
        assert item["snippet"] == ""
        assert item["source_url"] == ""


class TestJdDetail:
    def test_detail_full_mapping(self):
        detail = jd_detail(_row(_snapshot()))
        assert detail["id"] == 42
        assert detail["title"] == "Python 开发工程师"
        assert detail["location"] == "北京"
        assert detail["source_id"] == "zhilian-42"
        assert detail["raw_text"].startswith("岗位职责")
        assert detail["position"] == ""  # 快照无 normalized_position，重算得空
        assert detail["is_desensitized"] is False

    def test_experience_range_text(self):
        detail = jd_detail(_row(_snapshot()))
        assert detail["extraction_summary"]["experience"] == "3-5年"

    def test_experience_range_open_bounds(self):
        snap = _snapshot(
            extraction={"experience_range": {"min_years": 3, "max_years": None}}
        )
        assert jd_detail(_row(snap))["extraction_summary"]["experience"] == "3年以上"

    def test_experience_range_missing(self):
        snap = _snapshot(extraction={})
        assert jd_detail(_row(snap))["extraction_summary"]["experience"] == ""

    def test_salary_and_education_summary(self):
        detail = jd_detail(_row(_snapshot()))
        assert detail["extraction_summary"]["salary_range"] == "1-1.3万"
        assert detail["extraction_summary"]["education_level"] == "本科"
