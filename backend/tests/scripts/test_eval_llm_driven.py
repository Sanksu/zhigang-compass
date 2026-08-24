"""LLM 驱动评测脚本辅助测试（PR9a2：指标纯函数 + 黄金集读取）。"""

import json
import sys

sys.path.insert(0, "backend")

from scripts import eval_llm_driven as ev


class TestEvalHelpers:
    def test_macro_f1_groups_by_gold(self):
        hits = [
            {"gold": "编程语言", "match": True},
            {"gold": "编程语言", "match": False},
            {"gold": "前端", "match": True},
        ]
        out = ev._macro_f1(hits)
        assert out["group_count"] == 2
        # 编程语言组 p=1/2 r=1/2 f1=0.5；前端组 f1=1.0 → macro=(0.5+1)/2=0.75
        assert out["macro_f1"] == 0.75

    def test_load_rows_missing_unknown_task(self):
        assert ev._load_rows("nonexistent_task") == []

    def test_load_rows_parses_frozen(self, tmp_path):
        from pathlib import Path as _Path

        (tmp_path / "classification_150.jsonl").write_text(
            json.dumps({"skill": "Python", "gold_category": "编程语言"}) + "\n",
            encoding="utf-8",
        )
        try:
            ev._GOLDEN_DIR = tmp_path  # 运行时替换（测试隔离）
            rows = ev._load_rows("classification")
            assert len(rows) == 1
            assert rows[0]["gold_category"] == "编程语言"
        finally:
            ev._GOLDEN_DIR = _Path("backend") / "data" / "golden_set" / "llm_driven"

    def test_eval_functions_listed(self):
        assert ev._FILES.keys() >= {"classification", "normalization", "relation"}
