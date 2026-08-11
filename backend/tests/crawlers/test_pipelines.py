"""CleaningPipeline 实习/兼职岗位源头过滤测试。

验证 _employment_reason 判断与 process_item 拦截行为（含词边界防误伤）。
"""

from datetime import date
from types import SimpleNamespace

import pytest
from scrapy.exceptions import DropItem

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))

from crawlers.items import JobItem
from crawlers.pipelines import (
    MIN_JD_LENGTH,
    QUALITY_REVIEW_THRESHOLD,
    CleaningPipeline,
    _employment_reason,
    jd_decay_weight,
    jd_quality_score,
    normalize_post_date,
)


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
        company="XX科技", location="北京", salary="20-40K",
        description="负责后端服务开发、接口设计与性能优化，维护高可用系统。",
        requirements="熟悉 Python/Go，掌握 Linux 与数据库，3 年以上经验优先。",
    )
    result = pipe.process_item(item)
    assert result is item
    assert pipe._filtered_count == 0
    assert item["_fingerprint"]  # 正常岗位继续走指纹计算
    assert "quality" in item and "decay_weight" in item  # §4.2 质量评分/时效加权写入


# ── §4.2 质量过滤（长度过滤 → 质量评分 → 时效加权）──


def test_drops_short_jd():
    pipe = CleaningPipeline()
    pipe.crawler = SimpleNamespace(
        spider=SimpleNamespace(logger=SimpleNamespace(info=lambda *a, **k: None))
    )
    # 全文（title+description+requirements+raw_text）< 50 字 → 丢弃
    item = _job(
        title="Java 工程师", source="boss", source_id="3",
        description="负责开发",
    )
    with pytest.raises(DropItem) as exc:
        pipe.process_item(item)
    assert "过短" in str(exc.value)


def test_jd_text_length_not_enough_to_drop():
    # 恰好 50 字的完整文本不应被丢弃（边界）
    item = _job(
        title="测试工程师", source="boss", source_id="4",
        description="负责测试" + "流程设计与执行。" * 6,
    )
    assert len(item.get("title", "") + item.get("description", "")) >= MIN_JD_LENGTH


def test_quality_score_high_for_complete_jd():
    score = jd_quality_score(
        title="Python 后端开发工程师", company="XX科技", location="北京", salary="20-40K",
        description="负责后端服务开发、接口设计与性能优化，维护高可用系统。",
        requirements="熟悉 Python/Go，掌握 Linux 与数据库，3 年以上经验优先。",
    )
    assert score >= QUALITY_REVIEW_THRESHOLD


def test_quality_score_low_for_sparse_jd():
    # 仅标题 + 极短正文 → 字段完整度/长度/核心词均低分 → < 0.6
    score = jd_quality_score(title="Python 工程师", description="hi")
    assert score < QUALITY_REVIEW_THRESHOLD


def test_quality_score_format_penalty():
    # 乱码（同一字符连续 20+）→ 格式规范维度 0.5，总分被拉低
    noise = "啊" * 30
    score = jd_quality_score(
        title="后端工程师", company="X", description=noise,
        requirements="负责后端开发与维护，熟悉 Linux。",
    )
    assert score < 0.9


def test_quality_score_counts_raw_text():
    # 只填 raw_text 的源（如 maimai）：正文计入长度/核心词维度，避免评分失真
    body = "负责数据采集、清洗与建模，熟悉 SQL/Python，具备数据分析经验。"
    score = jd_quality_score(
        title="数据分析师", company="X", location="北京", salary="20-40K",
        raw_text=body,
    )
    assert score >= QUALITY_REVIEW_THRESHOLD
    # 无正文时评分显著更低（证明 raw_text 计入长度/核心词维度）
    bare = jd_quality_score(title="数据分析师", company="X", location="北京", salary="20-40K")
    assert score > bare


def test_decay_weight_fresh():
    from datetime import date

    assert jd_decay_weight("2026-07-20", today=date(2026, 8, 5)) == 1.0  # ≤30 天
    assert jd_decay_weight("2026-07-01", today=date(2026, 8, 5)) < 1.0   # >30 天衰减


def test_decay_weight_no_date():
    assert jd_decay_weight(None) == 1.0
    assert jd_decay_weight("") == 1.0


def test_decay_weight_math():
    from datetime import date

    import math

    # 60 天 → exp(-0.01×30)
    assert jd_decay_weight("2026-06-01", today=date(2026, 7, 31)) == pytest.approx(
        math.exp(-0.3), rel=1e-3
    )


# ── post_date 归一化（多源格式 → 统一可解析）──


def test_normalize_post_date_keeps_iso_dates():
    """ISO date / 空格分隔 datetime / ISO8601 原样保留。"""
    assert normalize_post_date("2026-08-06") == "2026-08-06"
    assert normalize_post_date("2026-08-09 00:27:24") == "2026-08-09 00:27:24"
    assert normalize_post_date("2026-08-09T00:27:24+08:00") == "2026-08-09T00:27:24+08:00"


def test_normalize_post_date_relative_days():
    """glassdoor 相对天数 → 绝对日期。"""
    today = date(2026, 8, 11)
    assert normalize_post_date("3d", today=today) == "2026-08-08"
    assert normalize_post_date("30d+", today=today) == "2026-07-12"


def test_normalize_post_date_relative_weeks():
    """相对周 → 绝对日期。"""
    today = date(2026, 8, 11)
    assert normalize_post_date("2w", today=today) == "2026-07-28"


def test_normalize_post_date_today_yesterday():
    today = date(2026, 8, 11)
    assert normalize_post_date("Today", today=today) == "2026-08-11"
    assert normalize_post_date("today", today=today) == "2026-08-11"
    assert normalize_post_date("Yesterday", today=today) == "2026-08-10"
    assert normalize_post_date("今天", today=today) == "2026-08-11"
    assert normalize_post_date("昨天", today=today) == "2026-08-10"


def test_normalize_post_date_unparseable_kept():
    """无法解析的值原样保留，不强行改写。"""
    assert normalize_post_date("") == ""
    assert normalize_post_date(None) == ""
    assert normalize_post_date("随时") == "随时"


def test_process_item_normalizes_post_date():
    """process_item 将相对时间 post_date 归一化后落 snapshot，decay 随之可算。"""
    pipe = CleaningPipeline()
    pipe.crawler = SimpleNamespace(
        spider=SimpleNamespace(logger=SimpleNamespace(info=lambda *a, **k: None))
    )
    item = _job(
        title="Python 后端开发工程师", source="glassdoor", source_id="gd-1",
        company="XX", location="NY", salary="$100K",
        description="负责后端服务开发与接口设计，维护系统稳定运行，熟悉云服务。",
        requirements="熟悉 Python，掌握数据库，3 年以上经验优先。",
        post_date="5d",
    )
    result = pipe.process_item(item)
    assert result["post_date"] == normalize_post_date("5d")
    # 相对时间归一化后 decay 不再恒为 1.0（5 天内 → 1.0，但可被正确解析）
    assert result["decay_weight"] == 1.0


def test_process_item_marks_needs_review():
    pipe = CleaningPipeline()
    pipe.crawler = SimpleNamespace(
        spider=SimpleNamespace(logger=SimpleNamespace(info=lambda *a, **k: None))
    )
    item = _job(
        title="Python 工程师", source="boss", source_id="5",
        description="负责 Python 后端服务开发与接口设计，维护系统稳定运行。",
        requirements="熟悉 Python、Flask/Django，掌握数据库，有项目经验者优先。",
    )
    result = pipe.process_item(item)
    assert "quality" in result
    assert result["needs_review"] == (result["quality"] < QUALITY_REVIEW_THRESHOLD)
    assert result["decay_weight"] == 1.0  # 无 post_date 不惩罚


# ── PostgresPipeline._upsert snapshot 合并 ──


class TestUpsertSnapshotMerge:
    """_upsert 冲突更新时 snapshot 为 JSONB 合并而非整体覆盖。

    回归：整体覆盖会在每次重爬时丢弃已有 extraction/validation 等下游写入，
    导致 ETL 重复 LLM 抽取已处理记录。
    """

    @staticmethod
    def _captured_sql(item: dict) -> str:
        import asyncio

        from app.models.raw import JDRaw
        from crawlers.pipelines import PostgresPipeline

        class _FakeSession:
            def __init__(self):
                self.stmts = []

            async def execute(self, stmt):
                self.stmts.append(stmt)

        session = _FakeSession()
        asyncio.run(PostgresPipeline._upsert(session, JDRaw, item))
        return str(session.stmts[0]).lower()

    def test_upsert_merges_snapshot_not_overwrite(self):
        sql = self._captured_sql({"source": "zhilian", "source_id": "1", "title": "Python 后端"})
        # 冲突更新时 snapshot 列 = 已有 || 新值（JSONB 右覆盖左同键）
        assert "snapshot || excluded.snapshot" in sql
        # 不应退回整体覆盖
        assert "snapshot = excluded.snapshot" not in sql
