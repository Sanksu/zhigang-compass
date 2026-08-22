"""岗位归一化与技能等级语义冻结评测（纯规则、离线可复现）。

不调用 LLM，因而不将规则结果表述为 LLM 准确率。岗位归一化样本覆盖中文、英文、
混合标题、技术栈限定、泛化岗位技能路由及拒绝；技能等级评测复用生产规范化规则，
同时统计合法等级与 weak 判定结果。

用法（cwd=backend）：
    uv run python scripts/evaluate_position_semantics.py
    uv run python scripts/evaluate_position_semantics.py --out reports/position_semantics_eval.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.extraction.dictionary import normalize_position_name
from app.services.proficiency import (
    CANONICAL_PROFICIENCY_LEVELS,
    normalize_proficiency_level,
    proficiency_is_weak,
)

_POSITION_GOLDEN = ROOT / "data" / "golden_set" / "position_normalization_frozen.jsonl"
_PROFICIENCY_GOLDEN = ROOT / "data" / "golden_set" / "proficiency_semantics_frozen.jsonl"
_DEFAULT_OUT = ROOT / "reports" / "position_semantics_eval.json"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load nonempty JSONL rows and reject malformed evaluation inputs."""
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_number} 不是合法 JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}:{line_number} 必须为对象")
        rows.append(row)
    if not rows:
        raise ValueError(f"冻结样本为空: {path}")
    return rows


def evaluate_positions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate deterministic position normalization against frozen samples."""
    results: list[dict[str, Any]] = []
    by_category: Counter[str] = Counter()
    passed_by_category: Counter[str] = Counter()
    for row in rows:
        expected = row.get("expected")
        if not isinstance(expected, str):
            raise ValueError(f"{row.get('id', '?')} 缺少字符串 expected")
        category = str(row.get("category") or "未分类")
        predicted = normalize_position_name(
            str(row.get("position_name") or ""),
            skills=[str(skill) for skill in row.get("skills") or []],
        )
        passed = predicted == expected
        by_category[category] += 1
        passed_by_category[category] += passed
        results.append({
            "id": row.get("id", ""),
            "category": category,
            "expected": expected,
            "predicted": predicted,
            "passed": passed,
        })
    return {
        "method": "冻结样本 + normalize_position_name（规则，非 LLM）",
        "samples": len(rows),
        "passed": sum(item["passed"] for item in results),
        "accuracy": round(sum(item["passed"] for item in results) / len(rows), 4),
        "by_category": {
            category: {"samples": count, "passed": passed_by_category[category]}
            for category, count in sorted(by_category.items())
        },
        "failures": [item for item in results if not item["passed"]],
    }


def evaluate_proficiency(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate canonical levels and weak semantics using existing production rules."""
    results: list[dict[str, Any]] = []
    legal_levels: Counter[str] = Counter()
    invalid_nonempty = 0
    weak_count = 0
    for row in rows:
        raw_level = row.get("level")
        expected_level = row.get("expected_level")
        expected_weak = row.get("expected_weak")
        if expected_level is not None and not isinstance(expected_level, str):
            raise ValueError(f"{row.get('id', '?')} expected_level 必须为字符串或 null")
        if not isinstance(expected_weak, bool):
            raise ValueError(f"{row.get('id', '?')} 缺少布尔 expected_weak")
        normalized = normalize_proficiency_level(raw_level)
        weak = proficiency_is_weak(raw_level, row.get("candidate_level"))
        if normalized in CANONICAL_PROFICIENCY_LEVELS:
            legal_levels[normalized] += 1
        elif isinstance(raw_level, str) and raw_level.strip():
            invalid_nonempty += 1
        weak_count += weak
        results.append({
            "id": row.get("id", ""),
            "raw_level": raw_level,
            "normalized_level": normalized,
            "weak": weak,
            "passed": normalized == expected_level and weak == expected_weak,
        })
    passed = sum(item["passed"] for item in results)
    return {
        "method": "冻结样本 + proficiency 生产规则（规则，非 LLM）",
        "samples": len(rows),
        "passed": passed,
        "accuracy": round(passed / len(rows), 4),
        "legal_levels": dict(sorted(legal_levels.items())),
        "invalid_nonempty_levels": invalid_nonempty,
        "weak_results": weak_count,
        "failures": [item for item in results if not item["passed"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="岗位归一化与技能等级语义冻结评测（离线规则）")
    parser.add_argument("--position-golden", type=Path, default=_POSITION_GOLDEN)
    parser.add_argument("--proficiency-golden", type=Path, default=_PROFICIENCY_GOLDEN)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = parser.parse_args()

    position = evaluate_positions(_load_jsonl(args.position_golden))
    proficiency = evaluate_proficiency(_load_jsonl(args.proficiency_golden))
    report = {
        "kind": "deterministic_semantics_evaluation",
        "position_normalization": position,
        "proficiency": proficiency,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"岗位归一化: {position['passed']}/{position['samples']} ({position['accuracy']:.2%})")
    print(
        "技能等级: "
        f"{proficiency['passed']}/{proficiency['samples']} ({proficiency['accuracy']:.2%}) | "
        f"合法等级={proficiency['legal_levels']} | "
        f"非法非空={proficiency['invalid_nonempty_levels']} | weak={proficiency['weak_results']}"
    )
    print(f"报告: {args.out}")
    return 0 if not position["failures"] and not proficiency["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
