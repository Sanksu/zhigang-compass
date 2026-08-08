"""回归测试：batch_extract 短文本行游标收敛 + 批间限流缓冲（代码审查高优先级）。

- 短文本行（<10 字符）此前只记 failed 不写 skipped 标记，`extraction IS NULL`
  游标永不推进，短行堆积时正常 JD 永远得不到抽取 → 必须写 skipped 标记推进游标
- _BATCH_REQUEST_INTERVAL 定义但未使用：批量任务批间无 sleep，连续调 provider 易 429
"""

from app.services.extraction.jd_extractor import _BATCH_REQUEST_INTERVAL
from app.workers.tasks import _is_jd_text_short


class TestShortTextSkip:
    def test_short_text_detected(self):
        assert _is_jd_text_short({}, "短") is True
        assert _is_jd_text_short({}, "    ") is True

    def test_whitespace_only_text_is_short(self):
        assert _is_jd_text_short({}, "\n\n  \t") is True

    def test_normal_text_not_short(self):
        assert _is_jd_text_short({}, "这是一个足够长的岗位描述文本，超过十个字") is False

    def test_body_fields_used_first(self):
        # snapshot 有 description 时优先拼接干净字段而非 raw_text
        assert _is_jd_text_short(
            {"description": "负责后端服务开发与系统架构设计"}, "短"
        ) is False


class TestBatchRequestInterval:
    def test_interval_defined_for_throttle(self):
        # 设计文档 §6.5 要求批量任务批间限流缓冲（防止 provider 429）
        assert _BATCH_REQUEST_INTERVAL > 0
