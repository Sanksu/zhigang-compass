"""图谱健康自动治理脚本测试（08-16 治理流程固化）。

覆盖纯函数部分（语言对判定 / 备份）与阶段 B 的 NULL 保护防回归
（r.similarity IS NOT NULL——2026-08-16 修正：新入图边无属性，
IS NULL 算脏会误删 Transact-SQL 类真实技能）。
"""

import inspect
import json

from scripts.graph_health_cleanup import (
    _backup,
    _same_language,
    stage_b_isolated,
)


class TestSameLanguage:
    def test_chinese_pair(self):
        assert _same_language("机器学习", "Python程序设计")

    def test_english_pair(self):
        assert _same_language("Kubernetes", "Docker and Kubernetes Masterclass")

    def test_cross_language_pair(self):
        # 跨语言对跳过（SBERT 跨语言 sim 天然低，不按同语言阈值判脏）
        assert not _same_language("机器学习", "Machine Learning with Python")

    def test_mixed_skill_chinese_course(self):
        assert not _same_language("Transact-SQL", "数据库编程")


class TestBackup:
    def test_writes_jsonl(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scripts.graph_health_cleanup.ROOT", tmp_path)
        rows = [{"name": "教学", "sid": "sk_1"}, {"name": "辅导", "sid": "sk_2"}]
        path = _backup(rows, "teaching_skills")
        assert path.exists()
        loaded = [json.loads(l) for l in path.open(encoding="utf-8")]
        assert loaded == rows


class TestStageBNullProtection:
    def test_cypher_excludes_null_similarity(self):
        """阶段 B 只计有 similarity 属性的边（NULL = 新边未评估，不算脏）。

        防回归：去掉 IS NOT NULL 会让 batch_extract 新技能（课程边无属性）
        全部进入"全脏边"判定，误删真实技能（08-16 实测 1244 个误判候选）。
        """
        src = inspect.getsource(stage_b_isolated)
        assert "r.similarity IS NOT NULL" in src
        assert "x.similarity < {SUSPICIOUS}" in src
