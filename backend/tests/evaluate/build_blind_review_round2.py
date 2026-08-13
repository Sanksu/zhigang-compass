"""构造盲审 round2 盲标工作簿（AI 抽取草稿 + 待人工复核）。

背景：盲审 round1 仅 12 条（8-10 候选池只有智联真实正文可用），M5 收尾
JD 盲审 F1 ≥ 0.90 需扩充盲审集至 30+ 条。round2 从采集快照
（data/crawlers/output/zhilian_*.jsonl，详情正文补抓后 322 条 >300 字）
按岗位类型多样性挑选 20 条，与 round1 12 条互补。

gold 草稿来源：现有 JDExtractor（LLM）对真实正文抽取——仅作人工复核
起点，review_status=AI预标_待复核，未经人工定稿不得作为评测 gold
（盲审 gold 独立性由人工复核保证）。

用法：
    uv run -- python tests/evaluate/build_blind_review_round2.py
    uv run -- python tests/evaluate/build_blind_review_round2.py --candidates 路径 --out 路径
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from openpyxl import Workbook

from app.services.extraction.jd_extractor import JDExtractor

# round1 工作簿 24 列（与 jd_manual_review_round1.xlsx Round1盲标 完全一致）
COLUMNS = [
    "sample_id", "source", "source_id", "source_url", "job_title_raw",
    "company", "location", "detail_raw_text",
    "current_gold_title", "review_gold_title",
    "current_gold_skills", "review_gold_skills",
    "current_gold_bonus_skills", "review_gold_bonus_skills",
    "current_gold_experience", "review_gold_experience",
    "current_gold_education", "review_gold_education",
    "current_gold_core_duties", "review_gold_core_duties",
    "review_status", "error_type", "review_note", "annotator",
]
SHEET_NAME = "Round2盲标"


def extract_draft(extractor: JDExtractor, text: str) -> dict[str, str]:
    """单条 JD 抽取草稿 → review_gold_* 字段（六字段口径对齐 round1）。

    映射：title ← position_name；skills ← skills[].name 去重；
    education ← education.level；core_duties ← typical_scenarios 短语。
    experience/bonus 抽取器无对应输出，留空供人工补。
    """
    result = extractor.extract(text)
    skills = list(dict.fromkeys(s.name for s in result.skills if s.name))
    duties = [f"{s.name}：{s.description}" if s.description else s.name
              for s in result.typical_scenarios]
    return {
        "review_gold_title": result.position_name or "",
        "review_gold_skills": json.dumps(skills, ensure_ascii=False),
        "review_gold_bonus_skills": "[]",
        "review_gold_experience": "",
        "review_gold_education": (result.education.level or "") if result.education else "",
        "review_gold_core_duties": json.dumps(duties[:8], ensure_ascii=False),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path,
                        default=ROOT / "data" / "golden_set" / "review" / "round2_candidates.jsonl")
    parser.add_argument("--out", type=Path,
                        default=ROOT / "data" / "golden_set" / "review" / "jd_manual_review_round2.xlsx")
    parser.add_argument("--cache", type=Path,
                        default=ROOT / "data" / "golden_set" / "review" / "round2_drafts_cache.json",
                        help="抽取草稿缓存（存在则跳过 LLM，改生成格式不必重跑抽取）")
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.candidates.read_text(encoding="utf-8").splitlines() if line.strip()]
    cache: dict[str, dict[str, str]] = {}
    if args.cache.exists():
        cache = json.loads(args.cache.read_text(encoding="utf-8"))
        print(f"读取草稿缓存 {len(cache)} 条，命中条目不重复调用 LLM")

    extractor = None
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    ws.append(COLUMNS)

    fail = 0
    for i, r in enumerate(rows, 1):
        text = f"{r.get('description') or ''}\n{r.get('requirements') or ''}"
        sample_id = f"r2_{i:03d}"
        row = {
            "sample_id": sample_id,
            "source": r.get("source") or "zhilian",
            "source_id": r.get("source_id") or "",
            "source_url": r.get("source_url") or "",
            "job_title_raw": r.get("title") or "",
            "company": r.get("company") or "",
            "location": r.get("location") or "",
            "detail_raw_text": text,
            "current_gold_title": "", "review_gold_title": "",
            "current_gold_skills": "", "review_gold_skills": "",
            "current_gold_bonus_skills": "", "review_gold_bonus_skills": "",
            "current_gold_experience": "", "review_gold_experience": "",
            "current_gold_education": "", "review_gold_education": "",
            "current_gold_core_duties": "", "review_gold_core_duties": "",
            "review_status": "AI预标_待复核",
            "error_type": "", "review_note": "", "annotator": "",
        }
        if sample_id in cache:
            row.update(cache[sample_id])
            print(f"  [{i}/{len(rows)}] {sample_id} 命中缓存")
        else:
            if extractor is None:
                print("开始 LLM 抽取草稿（20 条约 2-4 分钟）……")
                extractor = JDExtractor()
            try:
                draft = extract_draft(extractor, text)
                row.update(draft)
                cache[sample_id] = draft
                print(f"  [{i}/{len(rows)}] {sample_id} {r.get('title')!r} → {draft['review_gold_title']!r} "
                      f"skills={len(json.loads(draft['review_gold_skills']))}")
            except Exception as exc:  # 单条失败不阻塞，留空草稿供人工补标
                fail += 1
                row["error_type"] = f"{type(exc).__name__}: {exc}"
                row["review_note"] = "抽取草稿失败，需人工全量标注"
                print(f"  [{i}/{len(rows)}] {sample_id} 抽取失败: {exc}")

        # 空串写 None：openpyxl 对空串输出无 m:is 的空 inlineStr，评测脚本
        # _load_round1_blind_rows 解析不了；None 输出无 t 属性的空单元格（对齐 round1 产物）
        ws.append([(row[c] if row[c] not in ("", None) else None) for c in COLUMNS])

    if extractor is not None:
        args.cache.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(args.out)
    print(f"已生成 {args.out}（{len(rows)} 条，失败 {fail}）")
    print("提醒：review_gold_* 为 AI 抽取草稿，须人工复核定稿后方可作盲审 gold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
