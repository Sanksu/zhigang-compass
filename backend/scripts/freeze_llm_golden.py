"""LLM 驱动黄金集冻结脚本（PR9a1：从权威事实源派生，免人工标注依赖）。

派生源（全部为仓库内确定性事实）：
- 分类：configs/skill_whitelist.yaml（skill → 权威 category）——分层抽样
  覆盖全部现行类别 + 短 ASCII 词/中文名/中英混合切片
- 归一：SKILL_ALIAS（dictionary_data）变体→标准名对（gold=merge 到标准名）
  + 白名单短词切片（gold=keep，防 SBERT/LLM 误并）
- 关系：configs/skill_prerequisites.yaml（PREREQUISITE_OF 先修→目标）+
  configs/skill_relations.yaml（BELONGS_TO parent / ALTERNATIVE_OF 对称）
  + 跨类无关对（gold=NONE，note 标注规则推断待人工抽样确认）

输出（幂等，--seed 固定抽样）：
    data/golden_set/llm_driven/{classification_150,normalization_150,relation_100}.jsonl
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("freeze_llm_golden")

_OUTPUT_DIR = Path(_BACKEND_DIR) / "data" / "golden_set" / "llm_driven"

# 纯 ASCII 短词（≤6）切片：白名单标准名，gold=keep（防误并）
_ASCII_SHORT_RE = None


def _is_ascii_short(name: str) -> bool:
    import re

    return bool(re.match(r"^[A-Za-z]{1,6}$", name))


def classification_samples(
    whitelist: dict[str, str], category_size: int = 7, short_size: int = 15, seed: int = 42
) -> list[dict]:
    """按类分层抽样 + 短词切片（固定 seed 幂等）。"""
    import random

    rng = random.Random(seed)
    by_category: dict[str, list[str]] = {}
    for name, category in whitelist.items():
        by_category.setdefault(category, []).append(name)
    samples: list[dict] = []
    for category, names in sorted(by_category.items(), key=lambda kv: -len(kv[1])):
        picked = rng.sample(sorted(names), min(category_size, len(names)))
        for name in picked:
            tier = "short_ascii" if _is_ascii_short(name) else ("cjk" if any("\u4e00" <= c <= "\u9fff" for c in name) else "mixed")
            samples.append({
                "skill": name, "gold_category": category,
                "slice": tier, "category_size": len(names),
            })
    # 短词切片保底补齐（防止小类抽样后短词不足）
    short_in = [s for s in samples if s["slice"] == "short_ascii"]
    if len(short_in) < short_size:
        all_short = sorted(n for n in whitelist if _is_ascii_short(n) and n not in {s["skill"] for s in samples})
        for name in rng.sample(all_short, min(short_size - len(short_in), len(all_short))):
            samples.append({
                "skill": name, "gold_category": whitelist[name],
                "slice": "short_ascii", "category_size": 0,
            })
    return samples[:150]


def normalization_pairs(alias: dict[str, str], seed: int = 42) -> list[dict]:
    """别名变体→标准名 merge 对 + 白名单短词 keep 切片。"""

    merge = [
        {"variant": k, "gold_standard": v, "gold_action": "merge", "slice": "alias"}
        for k, v in sorted(alias.items())
        if k != v and v.strip() and k.strip()
    ]
    # 去重（同 variant 只留一条），限制 140 merge 对
    seen: set[str] = set()
    merge_dedup = []
    for item in merge:
        if item["variant"] in seen:
            continue
        seen.add(item["variant"])
        merge_dedup.append(item)
        if len(merge_dedup) >= 140:
            break
    keep_slice = [
        {"variant": n, "gold_standard": n, "gold_action": "keep", "slice": "short_ascii"}
        for n in sorted(_whitelist_short_names())
        if len(n) <= 6
    ][:10]
    return merge_dedup + keep_slice


def _whitelist_short_names() -> list[str]:
    """白名单内纯 ASCII 短词（标准名集合，keep 切片来源）。"""
    # 延迟导入避免循环
    from app.services.extraction.dictionary import SKILL_WHITELIST

    return [n for n in SKILL_WHITELIST if _is_ascii_short(n)]


def relation_pairs(seed: int = 42) -> list[dict]:
    """先修/父子/替代关系 gold 对 + 跨类 NONE 对照。"""
    import random

    from app.services.extraction.dictionary import SKILL_WHITELIST
    from app.services.kg.skill_relations import _CONFIG_PREREQ, _CONFIG_RELATIONS, _load_yaml

    rng = random.Random(seed)
    prereq_cfg = _load_yaml(_CONFIG_PREREQ)
    relation_cfg = _load_yaml(_CONFIG_RELATIONS)
    items: list[dict] = []
    for name, entry in ((prereq_cfg.get("skills") or {}).items()):
        for pre in (entry.get("prerequisites") or []):
            items.append({
                "source": pre, "target": name, "gold_relation": "PREREQUISITE_OF",
                "gold_direction": "a_to_b", "source_note": "skill_prerequisites.yaml",
            })
    for name, entry in ((relation_cfg.get("skills") or {}).items()):
        for parent in (entry.get("parent") or []):
            items.append({
                "source": name, "target": parent, "gold_relation": "BELONGS_TO",
                "gold_direction": "a_to_b", "source_note": "skill_relations.yaml",
            })
        for alt in (entry.get("alternatives") or []):
            items.append({
                "source": name, "target": alt, "gold_relation": "ALTERNATIVE_OF",
                "gold_direction": "symmetric", "source_note": "skill_relations.yaml",
            })
    # NONE 对照：跨大类白名单对（规则推断，note 标注待人工抽样确认）
    categories: dict[str, list[str]] = {}
    for name in SKILL_WHITELIST:
        categories.setdefault(_LOAD_CATEGORY(name), []).append(name)
    none_pairs: list[dict] = []
    cat_keys = sorted(categories)
    for i in range(min(5, len(cat_keys))):
        a, b = cat_keys[i], cat_keys[-(i + 1)]
        if a == b:
            continue
        sa = rng.sample(sorted(categories[a]), 1)[0]
        sb = rng.sample(sorted(categories[b]), 1)[0]
        none_pairs.append({
            "source": sa, "target": sb, "gold_relation": "NONE",
            "gold_direction": "a_to_b", "source_note": "跨类规则推断，待人工抽样确认",
        })
    # 限额：先修 ≤55 / 父子·替代 ≤35 / NONE 10
    return items[:55] + [i for i in items[55:] if i.get("source_note") == "skill_relations.yaml"][:35] + none_pairs[:10]


def _LOAD_CATEGORY(name: str) -> str:
    """白名单技能 → 权威类别（供 NONE 对照跨类选取）。"""
    from app.services.extraction.dictionary import SKILL_CATEGORY

    return SKILL_CATEGORY.get(name, "未分类")


def main() -> None:
    parser = argparse.ArgumentParser(description="派生并冻结 LLM 驱动黄金集（确定性 gold）")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    from app.services.extraction.dictionary import SKILL_WHITELIST, SKILL_CATEGORY
    from app.services.extraction.dictionary_data import SKILL_ALIAS

    whitelist = {
        name: SKILL_CATEGORY[name] for name in SKILL_WHITELIST if name in SKILL_CATEGORY
    }
    class_samples = classification_samples(whitelist, seed=args.seed)
    norm_samples = normalization_pairs(SKILL_ALIAS, seed=args.seed)
    rel_samples = relation_pairs(seed=args.seed)

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        "classification_150.jsonl": class_samples,
        "normalization_150.jsonl": norm_samples,
        "relation_100.jsonl": rel_samples,
    }
    for fname, rows in files.items():
        (Path(_OUTPUT_DIR) / fname).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
            encoding="utf-8",
        )
        categories = Counter(r.get("gold_category") for r in rows)
        category_names = ",".join(sorted(c for c in categories if c is not None))[:120]
        logger.info("%s: %d 条, 覆盖 %d 类 (%s)", fname, len(rows), len(categories), category_names)
    print(f"已冻结至 {_OUTPUT_DIR}")


if __name__ == "__main__":
    main()