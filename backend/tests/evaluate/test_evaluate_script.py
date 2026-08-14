"""准确率评测脚本测试（AL-M4-04，设计文档 §13.3）。

使用仓库内真实黄金集验证脚本可运行且输出结构稳定（数据确定性，非 mock）。

注意：TestEvalResume 会触发真实 LLM 调用（ResumeExtractor），打
@pytest.mark.integration 标记，默认 pytest 运行排除；需显式 `pytest -m integration`
执行（见 pyproject.toml）。
"""

import pytest

from scripts.evaluate import (
    _top3_accuracy,
    eval_jd,
    eval_match,
    eval_resume,
    generate_html_report,
)


class TestEvalJd:
    def test_returns_expected_schema(self):
        r = eval_jd()
        assert r["task"] == "jd"
        assert r["skipped"] is False
        assert r["samples"] > 0
        assert 0.0 <= r["f1"] <= 1.0
        assert 0.0 <= r["precision"] <= 1.0
        assert 0.0 <= r["recall"] <= 1.0
        assert r["target_f1"] == 0.90
        assert "target_met" in r

    def test_jd_has_confusion_and_error_cases(self):
        """设计文档 §13.3：JD 报告含混淆矩阵 + 错误分析。"""
        r = eval_jd()
        assert "confusion" in r
        assert all(k in r["confusion"] for k in ("tp", "fp", "fn"))
        assert isinstance(r["error_cases"], list)


class TestEvalResume:
    """简历黄金集已交付（AL-M5-02）：resume 项真实评测并返回结构。

    无 LLM 配置环境（CI）走规则兜底，不崩溃、不伪造结果。
    """

    pytestmark = pytest.mark.integration

    def test_returns_expected_schema(self):
        r = eval_resume()
        assert r["task"] == "resume"
        assert r["skipped"] is False
        assert r["samples"] > 0
        assert 0.0 <= r["f1"] <= 1.0
        assert 0.0 <= r["precision"] <= 1.0
        assert 0.0 <= r["recall"] <= 1.0
        assert r["target_f1"] == 0.90
        assert "target_met" in r

    def test_resume_has_confusion(self):
        """设计文档 §13.3：简历报告含混淆矩阵。"""
        r = eval_resume()
        assert "confusion" in r
        assert all(k in r["confusion"] for k in ("tp", "fp", "fn"))


class TestEvalMatch:
    def test_rule_baseline_returns_expected_schema(self):
        r = eval_match(semantic=False)
        assert r["task"] == "match"
        assert r["skipped"] is False
        assert 0.0 <= r["spearman"] <= 1.0
        assert 0.0 <= r["accuracy"] <= 1.0
        assert r["target_accuracy"] == 0.90
        assert "target_met" in r

    def test_match_has_top3_and_confusion(self):
        """设计文档 §9.6/§13.3：匹配报告含 Top-3 准确率 + 混淆矩阵 + 错误样例。"""
        r = eval_match(semantic=False)
        assert "top3_accuracy" in r
        assert "top3_samples" in r
        assert "confusion" in r
        assert all(k in r["confusion"] for k in ("tp", "fp", "tn", "fn"))
        assert isinstance(r["error_cases"], list)

    def test_jd_golden_set_has_positive_samples(self):
        """黄金集样本足够（字段级评测有意义的前提）。

        设计文档硬性要求：JD 100 条（M3），此处作为回归护栏防缩水。
        """
        from tests.evaluate.run_baseline import load_golden_set

        from scripts.evaluate import _JD_GOLDEN

        items = load_golden_set(str(_JD_GOLDEN))
        assert len(items) >= 100

    def test_match_golden_set_has_positive_samples(self):
        """匹配黄金集规模护栏：100 岗位 × 3 候选（1 正 2 负）= 300 行。"""
        from tests.evaluate.run_baseline import load_golden_set

        from scripts.evaluate import _MATCH_GOLDEN

        items = load_golden_set(str(_MATCH_GOLDEN))
        assert len(items) >= 300
        assert any(item.get("label") == 1 for item in items)


class TestTop3Accuracy:
    """Top-3 推荐准确率单元测试（设计文档 §9.6/§13.3）。"""

    def test_perfect_ranking(self):
        """所有正样本都在 Top-3 → accuracy = 1.0。"""
        pairs = [
            {"candidate_skills": ["Python"], "position_id": "p1", "label": 1},
            {"candidate_skills": ["Python"], "position_id": "p2", "label": 0},
            {"candidate_skills": ["Python"], "position_id": "p3", "label": 0},
            {"candidate_skills": ["Python"], "position_id": "p4", "label": 0},
        ]
        # 正样本得分最高
        scores = [0.9, 0.3, 0.2, 0.1]
        acc, n = _top3_accuracy(pairs, scores)
        assert acc == 1.0
        assert n == 1

    def test_positive_outside_top3(self):
        """正样本排在 Top-3 之外 → accuracy = 0.0。"""
        pairs = [
            {"candidate_skills": ["Python"], "position_id": "p1", "label": 0},
            {"candidate_skills": ["Python"], "position_id": "p2", "label": 0},
            {"candidate_skills": ["Python"], "position_id": "p3", "label": 0},
            {"candidate_skills": ["Python"], "position_id": "p4", "label": 1},
        ]
        # 正样本得分最低
        scores = [0.9, 0.8, 0.7, 0.1]
        acc, n = _top3_accuracy(pairs, scores)
        assert acc == 0.0
        assert n == 1

    def test_no_eligible_candidates(self):
        """所有候选人对数 < 3 → 无合格候选人，返回 (None, 0)。"""
        pairs = [
            {"candidate_skills": ["Python"], "position_id": "p1", "label": 1},
            {"candidate_skills": ["Python"], "position_id": "p2", "label": 0},
        ]
        scores = [0.9, 0.1]
        acc, n = _top3_accuracy(pairs, scores)
        assert acc is None
        assert n == 0

    def test_candidate_without_positives_skipped(self):
        """候选人无正样本 → 跳过，不计入分母。"""
        pairs = [
            {"candidate_skills": ["Python"], "position_id": "p1", "label": 0},
            {"candidate_skills": ["Python"], "position_id": "p2", "label": 0},
            {"candidate_skills": ["Python"], "position_id": "p3", "label": 0},
        ]
        scores = [0.9, 0.5, 0.1]
        acc, n = _top3_accuracy(pairs, scores)
        assert acc is None
        assert n == 0

    def test_multiple_candidates_mixed(self):
        """多个候选人混合情况：1 个命中 + 1 个未命中 → accuracy = 0.5。"""
        pairs = [
            # 候选人 A：正样本在 Top-3（命中）
            {"candidate_skills": ["A"], "position_id": "a1", "label": 1},
            {"candidate_skills": ["A"], "position_id": "a2", "label": 0},
            {"candidate_skills": ["A"], "position_id": "a3", "label": 0},
            {"candidate_skills": ["A"], "position_id": "a4", "label": 0},
            # 候选人 B：正样本不在 Top-3（未命中）
            {"candidate_skills": ["B"], "position_id": "b1", "label": 0},
            {"candidate_skills": ["B"], "position_id": "b2", "label": 0},
            {"candidate_skills": ["B"], "position_id": "b3", "label": 0},
            {"candidate_skills": ["B"], "position_id": "b4", "label": 1},
        ]
        scores = [
            0.9, 0.3, 0.2, 0.1,  # A: positive at top
            0.9, 0.8, 0.7, 0.1,  # B: positive at bottom
        ]
        acc, n = _top3_accuracy(pairs, scores)
        assert acc == 0.5
        assert n == 2


class TestHtmlReport:
    """HTML 评测报告测试（设计文档 §13.3：分项得分+错误分析+混淆矩阵）。"""

    def _make_full_report(self) -> dict:
        """构造三项全跑的报告（含 Top-3 和错误样例）。"""
        return {
            "generated_at": "20260806_1200",
            "target": "三项准确率 ≥ 90%（设计文档 §13.3）",
            "results": [
                {
                    "task": "jd",
                    "skipped": False,
                    "method": "关键词基线（无 LLM，离线）",
                    "samples": 100,
                    "precision": 0.85,
                    "recall": 0.90,
                    "f1": 0.8724,
                    "target_f1": 0.90,
                    "target_met": False,
                    "confusion": {"tp": 200, "fp": 35, "fn": 22},
                    "error_cases": [
                        {"source_id": "jd_001", "false_positives": ["Docker"], "false_negatives": ["K8s"]},
                    ],
                },
                {
                    "task": "resume",
                    "skipped": False,
                    "method": "真实抽取（LLM + 规则兜底）",
                    "samples": 50,
                    "precision": 0.88,
                    "recall": 0.92,
                    "f1": 0.8998,
                    "target_f1": 0.90,
                    "target_met": False,
                    "confusion": {"tp": 150, "fp": 20, "fn": 13},
                },
                {
                    "task": "match",
                    "skipped": False,
                    "method": "规则匹配（无语义）",
                    "spearman": 0.82,
                    "accuracy": 0.88,
                    "target_accuracy": 0.90,
                    "target_met": False,
                    "top3_accuracy": 0.75,
                    "top3_samples": 8,
                    "confusion": {"tp": 40, "fp": 10, "tn": 35, "fn": 15},
                    "error_cases": [
                        {"position_id": "pos_001", "score": 0.42, "label": 1, "error_type": "FN"},
                    ],
                },
            ],
        }

    def test_full_report_contains_all_sections(self):
        """完整报告含三项详情 + 总览。"""
        report = self._make_full_report()
        html = generate_html_report(report)
        assert "JD 解析评测详情" in html
        assert "简历提取评测详情" in html
        assert "人岗匹配评测详情" in html
        assert "总览" in html

    def test_report_contains_confusion_matrix(self):
        """设计文档 §13.3：报告含混淆矩阵。"""
        report = self._make_full_report()
        html = generate_html_report(report)
        assert "混淆矩阵" in html
        assert "TP" in html
        assert "FP" in html
        assert "FN" in html
        assert "TN" in html

    def test_report_contains_error_analysis(self):
        """设计文档 §13.3：报告含错误分析。"""
        report = self._make_full_report()
        html = generate_html_report(report)
        assert "错误样例" in html
        assert "jd_001" in html
        assert "FN" in html

    def test_report_contains_top3_accuracy(self):
        """设计文档 §9.6：报告含 Top-3 推荐准确率。"""
        report = self._make_full_report()
        html = generate_html_report(report)
        assert "Top-3" in html
        assert "0.7500" in html

    def test_report_contains_badges(self):
        """达标/未达标徽章存在。"""
        report = self._make_full_report()
        html = generate_html_report(report)
        assert "未达标" in html

    def test_skipped_task_renders_skip_badge(self):
        """跳过的任务显示"跳过"徽章。"""
        report = {
            "generated_at": "20260806_1200",
            "target": "test",
            "results": [
                {"task": "jd", "skipped": True, "reason": "黄金集缺失"},
            ],
        }
        html = generate_html_report(report)
        assert "跳过" in html
        assert "黄金集缺失" in html

    def test_match_top3_none_renders_na(self):
        """match 的 top3_accuracy 为 None 时显示 N/A。"""
        report = self._make_full_report()
        report["results"][2]["top3_accuracy"] = None
        html = generate_html_report(report)
        assert "N/A" in html

    def test_html_is_valid_structure(self):
        """HTML 是有效的文档结构。"""
        report = self._make_full_report()
        html = generate_html_report(report)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html
        assert "<html" in html
        assert "<body>" in html
        assert "</body>" in html


class TestEvalJdLlm:
    """JD LLM 盲审归档读取（不触发真实 LLM；无归档时跳过不伪造）。

    归档由 tests/evaluate/run_manual_jd_eval.py --run 生成（reports/eval_jd_llm_*.json），
    evaluate.py 只读最近归档、不重跑（避免重复消耗 LLM 额度）。
    本测试用 tmp_path 构造归档验证读取/跳过/损坏判定与 HTML 渲染。
    """

    _ARCHIVE_TEMPLATE = {
        "task": "jd_llm",
        "method": "真实抽取（LLM + 规则兜底，12 条人工盲审）",
        "samples": 11,
        "fallback_samples": 1,
        "failed_samples": 0,
        "precision": 0.85,
        "recall": 0.90,
        "f1": 0.8740,
        "target_f1": 0.90,
        "target_met": False,
        "confusion": {"tp": 100, "fp": 20, "fn": 10},
        "bonus": {"tp": 5, "fp": 8, "fn": 20, "precision": 0.3846, "recall": 0.2000, "f1": 0.2632},
        "title_raw_exact_accuracy": 0.4545,
        "title_normalized_accuracy": 0.8182,
        "education_raw_exact_accuracy": 0.9091,
        "skills_average_sample_f1": 0.8500,
        "per_sample_skills_f1": [0.9, 0.8, 0.7],
        "per_sample_bonus_f1": [],
        "error_types": [["model-added skills not in human gold", 5]],
        "experience_gap": "Schema coverage gap: JDExtractionResult has no experience_range field",
        "core_duties_gap": "Schema coverage gap: JDExtractionResult has no core_duties field",
    }

    def _make_archive(self, report_dir, name="eval_jd_llm_20260812_0000.json"):
        import json

        from scripts import evaluate as ev

        path = report_dir / name
        path.write_text(
            json.dumps({"generated_at": "20260812_0000", "target": "JD 解析（LLM 盲审评测）", "results": [self._ARCHIVE_TEMPLATE]}),
            encoding="utf-8",
        )
        assert ev  # 保持与项目局部 import 风格一致（引用防止 lint 未使用）
        return path

    def test_reads_latest_archive(self, tmp_path, monkeypatch):
        import scripts.evaluate as ev

        self._make_archive(tmp_path, "eval_jd_llm_20260811_0000.json")
        self._make_archive(tmp_path, "eval_jd_llm_20260812_0000.json")
        monkeypatch.setattr(ev, "_REPORT_DIR", tmp_path)
        r = ev.eval_jd_llm()
        assert r["task"] == "jd_llm"
        assert r["skipped"] is False
        assert r["samples"] == 11
        # 取文件名排序最新的归档
        assert r["archive"] == "eval_jd_llm_20260812_0000.json"
        assert 0.0 <= r["f1"] <= 1.0
        assert "confusion" in r and "bonus" in r
        assert "error_types" in r

    def test_skips_when_no_archive(self, tmp_path, monkeypatch):
        import scripts.evaluate as ev

        monkeypatch.setattr(ev, "_REPORT_DIR", tmp_path)
        r = ev.eval_jd_llm()
        assert r["task"] == "jd_llm"
        assert r["skipped"] is True
        assert "run_manual_jd_eval.py" in r["reason"]

    def test_skips_on_corrupt_archive(self, tmp_path, monkeypatch):
        import scripts.evaluate as ev

        (tmp_path / "eval_jd_llm_20260812_0000.json").write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(ev, "_REPORT_DIR", tmp_path)
        r = ev.eval_jd_llm()
        assert r["skipped"] is True
        assert "归档损坏" in r["reason"]

    def test_skips_on_incomplete_archive(self, tmp_path, monkeypatch):
        """归档缺核心字段（如 f1）时视为损坏，不伪造结果。"""
        import json

        import scripts.evaluate as ev

        (tmp_path / "eval_jd_llm_20260812_0000.json").write_text(
            json.dumps({"generated_at": "x", "target": "y", "results": [{"task": "jd_llm"}]}),
            encoding="utf-8",
        )
        monkeypatch.setattr(ev, "_REPORT_DIR", tmp_path)
        r = ev.eval_jd_llm()
        assert r["skipped"] is True

    def test_html_contains_jd_llm_section(self, tmp_path, monkeypatch):
        import scripts.evaluate as ev

        self._make_archive(tmp_path)
        monkeypatch.setattr(ev, "_REPORT_DIR", tmp_path)
        report = {"generated_at": "20260812", "target": "x", "results": [ev.eval_jd(), ev.eval_jd_llm()]}
        html = ev.generate_html_report(report)
        assert "JD 解析（LLM 盲审）" in html
        assert "JD 解析评测详情 · LLM 盲审" in html
        assert "0.8740" in html
        assert "Schema 缺口" in html
        assert "model-added skills not in human gold" in html

    def test_skipped_jd_llm_renders_skip_row(self):
        """无归档时总览显示跳过徽章与生成方式说明。"""
        report = {
            "generated_at": "20260812_1200",
            "target": "test",
            "results": [
                {"task": "jd_llm", "skipped": True, "reason": "无 LLM 盲审归档。先执行: uv run ..."},
            ],
        }
        html = generate_html_report(report)
        assert "JD 解析（LLM 盲审）" in html
        assert "跳过" in html


class TestJdLlmArchive:
    """run_manual_jd_eval 归档函数（纯函数，不触发 LLM/不读 xlsx）。"""

    def _metrics_fixture(self):
        return {
            "total_samples": 12,
            "real_llm_success_samples": 11,
            "fallback_samples": 1,
            "failed_samples": 0,
            "title_raw_exact_accuracy": 0.4545454545,
            "title_normalized_accuracy": 0.8181818181,
            "skills_micro": {"tp": 100, "fp": 20, "fn": 10, "precision": 0.8333, "recall": 0.9091, "f1": 0.8696},
            "skills_average_sample_f1": 0.85,
            "bonus_skills_micro": {"tp": 5, "fp": 8, "fn": 20, "precision": 0.3846, "recall": 0.2, "f1": 0.2632},
            "bonus_skills_average_sample_f1": 0.2,
            "education_raw_exact_accuracy": 0.909090909,
            "experience": "Schema coverage gap: JDExtractionResult has no experience_range field",
            "core_duties": "Schema coverage gap: JDExtractionResult has no core_duties field",
            "per_sample_skills_f1": [0.9, 0.8, 0.7],
            "per_sample_bonus_f1": [],
            "error_types": [["model-added skills not in human gold", 5]],
        }

    def test_archive_metrics_writes_standard_report(self, tmp_path):
        """归档为 reports/eval_jd_llm_*.json，结构与 evaluate.py 报告同构。"""
        import json

        from tests.evaluate.run_manual_jd_eval import archive_metrics

        path = archive_metrics(self._metrics_fixture(), report_dir=tmp_path)
        assert path.name.startswith("eval_jd_llm_") and path.name.endswith(".json")
        report = json.loads(path.read_text(encoding="utf-8"))
        assert "generated_at" in report and "results" in report
        r = report["results"][0]
        assert r["task"] == "jd_llm"
        assert r["samples"] == 11
        assert r["f1"] == 0.8696
        assert r["confusion"] == {"tp": 100, "fp": 20, "fn": 10}
        # 0.8696 < 0.90 → 未达标（round 后判定，不因精度误报）
        assert r["target_met"] is False

    def test_archive_metrics_target_met(self, tmp_path):
        """F1 ≥ 0.90 时 target_met=True。"""
        import json

        from tests.evaluate.run_manual_jd_eval import archive_metrics

        m = self._metrics_fixture()
        m["skills_micro"]["f1"] = 0.9001
        r = json.loads(archive_metrics(m, report_dir=tmp_path).read_text(encoding="utf-8"))["results"][0]
        assert r["target_met"] is True

    def test_archive_result_keeps_auxiliary_metrics(self, tmp_path):
        """归档保留 title/education/bonus/per-sample 等展示字段。"""
        import json

        from tests.evaluate.run_manual_jd_eval import archive_metrics

        r = json.loads(archive_metrics(self._metrics_fixture(), report_dir=tmp_path).read_text(encoding="utf-8"))["results"][0]
        assert r["title_normalized_accuracy"] == 0.8182
        assert r["education_raw_exact_accuracy"] == 0.9091
        assert r["bonus"]["f1"] == 0.2632
        assert r["per_sample_skills_f1"] == [0.9, 0.8, 0.7]
        assert r["error_types"][0][0] == "model-added skills not in human gold"
        assert "experience_gap" in r and "core_duties_gap" in r


class TestGoldRevisions:
    """盲审 gold 口径修订（load_gold_revisions / apply_gold_revisions，纯函数）。"""

    def _revisions(self):
        return {
            "public_003": {"sample_id": "public_003", "move_skills_to_bonus": ["数据挖掘", "自然语言处理"]},
            "jd_030": {"sample_id": "jd_030", "remove_skills": ["大模型评测"]},
        }

    def test_apply_move_skills_to_bonus(self):
        from tests.evaluate.run_manual_jd_eval import apply_gold_revisions

        skills, bonus = apply_gold_revisions(
            self._revisions(), "public_003",
            ["Linux", "图计算", "数据挖掘", "机器学习", "自然语言处理"],
            ["Hive", "PyTorch"],
        )
        assert "数据挖掘" not in skills and "自然语言处理" not in skills
        assert skills == ["Linux", "图计算", "机器学习"]
        assert set(bonus) == {"Hive", "PyTorch", "数据挖掘", "自然语言处理"}

    def test_apply_remove_skills(self):
        from tests.evaluate.run_manual_jd_eval import apply_gold_revisions

        skills, bonus = apply_gold_revisions(
            self._revisions(), "jd_030",
            ["AIGC", "数据分析", "大模型评测"],
            ["Python", "SQL"],
        )
        assert skills == ["AIGC", "数据分析"]
        assert bonus == ["Python", "SQL"]  # 删除不进入 bonus

    def test_apply_noop_for_unknown_sample(self):
        from tests.evaluate.run_manual_jd_eval import apply_gold_revisions

        skills, bonus = apply_gold_revisions(self._revisions(), "public_999", ["A"], ["B"])
        assert skills == ["A"] and bonus == ["B"]

    def test_load_revisions_from_file(self):
        """修订文件缺失/损坏时不阻断（返回空）。"""
        from pathlib import Path

        from tests.evaluate.run_manual_jd_eval import load_gold_revisions

        assert load_gold_revisions(Path("nonexistent_dir/never.json")) == {}
        assert load_gold_revisions(Path(__file__)) == {}  # 非 JSON 文件

    def test_load_revisions_missing_file_ok(self, tmp_path):
        from tests.evaluate.run_manual_jd_eval import load_gold_revisions

        assert load_gold_revisions(tmp_path / "no.json") == {}
