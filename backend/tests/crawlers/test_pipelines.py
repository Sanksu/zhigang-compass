"""CleaningPipeline 实习/兼职岗位源头过滤测试。

验证 _employment_reason 判断与 process_item 拦截行为（含词边界防误伤）。
"""

from types import SimpleNamespace

import pytest
from scrapy.exceptions import DropItem

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))

from crawlers.items import JobItem
from crawlers.pipelines import CleaningPipeline, _employment_reason


def _job(**fields) -> JobItem:
    item = JobItem()
    for k, v in fields.items():
        item[k] = v
    return item


# ── _employment_reason 判断 ──


def test_intern_cn_title():
    assert _employment_reason(_job(title="实习生（前端开发）")) == "实习岗位"
    assert _employment_reason(_job(title="数据运营实习生")) == "实习岗位"


def test_intern_en_title():
    assert _employment_reason(_job(title="Software Engineer Intern")) == "实习岗位"
    assert _employment_reason(_job(title="Data Scientist Internship")) == "实习岗位"


def test_intern_tag():
    assert _employment_reason(_job(title="Engineer", tags=["INTERNSHIP"])) == "实习岗位"


def test_chinese_tag_not_filtered():
    # 智联 tags 为技能/招聘对象标签（如"金融分析大学生实习"），非就业类型，不应误拦截
    assert _employment_reason(
        _job(title="商业数据金融分析师", tags=["金融分析大学生实习"])
    ) is None


def test_parttime_cn_title():
    assert _employment_reason(_job(title="兼职数据分析")) == "兼职岗位"


def test_parttime_en_title():
    assert _employment_reason(_job(title="Data Analyst - Part Time")) == "兼职岗位"
    assert _employment_reason(_job(title="Part-time UI Designer")) == "兼职岗位"


def test_parttime_tag():
    assert _employment_reason(_job(title="Analyst", tags=["PART_TIME"])) == "兼职岗位"


def test_fulltime_not_filtered():
    assert _employment_reason(_job(title="Software Engineer", tags=["FULL_TIME"])) is None


def test_intern_no_false_positive():
    # intern 词边界：internal / internet / international 不应误伤
    assert _employment_reason(_job(title="International Sales Manager")) is None
    assert _employment_reason(_job(title="Internal Tool Developer")) is None
    assert _employment_reason(_job(title="IoT Internet Engineer")) is None


def test_normal_job_not_filtered():
    assert _employment_reason(
        _job(title="Python 后端开发工程师", company="XX科技", tags=["技能标签"])
    ) is None


# ── process_item 拦截行为 ──


def test_process_item_drops_intern_job():
    pipe = CleaningPipeline()
    pipe.crawler = SimpleNamespace(
        spider=SimpleNamespace(
            logger=SimpleNamespace(info=lambda *a, **k: None),
            name="test_spider",
        )
    )
    item = _job(title="实习生（前端开发）", source="boss", source_id="1")
    with pytest.raises(DropItem) as exc:
        pipe.process_item(item)
    assert "实习岗位" in str(exc.value)
    assert pipe._filtered_count == 1


def test_process_item_passes_normal_job():
    pipe = CleaningPipeline()
    pipe.crawler = SimpleNamespace(
        spider=SimpleNamespace(logger=SimpleNamespace(info=lambda *a, **k: None))
    )
    item = _job(
        title="Python 后端开发工程师", source="boss", source_id="2",
        company="XX科技", description="负责后端开发",
    )
    result = pipe.process_item(item)
    assert result is item
    assert pipe._filtered_count == 0
    assert item["_fingerprint"]  # 正常岗位继续走指纹计算
