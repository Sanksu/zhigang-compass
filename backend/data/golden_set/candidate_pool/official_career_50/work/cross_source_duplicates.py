#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S6 §十一：跨源完整三重去重（官网50 ↔ 智联candidate v1 ↔ Gold 110）→ 输出STRONG/WEAK 只报告不删除
位置：work/ 纯stdlib；
冻结判定规则（来自duplicate_report.md历史约定，不擅自改标准）：
  STRONG（强匹配/高度疑似重复）：
    - 同公司（公司名相似度≥0.9 或 核心公司词匹配：腾讯/Tencent ↔ 腾讯科技/腾讯；字节/ByteDance ↔ 北京字节跳动/字节）
    - AND 岗位title相似度 ≥ 0.75
    - AND responsibilities职责正文相似度 ≥ 0.65
  WEAK（弱匹配/可能重复）：
    - 满足以下任一：
      1. 同公司 AND title相似度 ≥ 0.60
      2. title相似度 ≥ 0.80（即使公司不完全匹配）
      3. responsibilities正文相似度 ≥ 0.80（即使title略低）
输出：
  work/cross_source_duplicates.jsonl：每行1条记录 {"level":"STRONG|WEAK","pair":[src1_meta,src2_meta],"scores":{...}}
  stdout 汇总 STRONG/WEAK 数量。
  注意：只报告，永不自动删除任何数据
"""
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
OFFICIAL_50 = ROOT / "official_career_50_clean.jsonl"
ZHILIAN = ROOT.parent / "v1" / "real_jd_candidates_clean.jsonl"
GOLD110 = ROOT.parent.parent / "final" / "jd_golden_110.jsonl"
OUT_JSONL = WORK / "cross_source_duplicates.jsonl"


def read_jsonl(p):
    rows, errs = [], []
    if not p.exists():
        print(f"[WARN] 文件不存在: {p}")
        return rows, errs
    with open(p, "r", encoding="utf-8") as fh:
        raw = fh.read()
    for lnno, pl in enumerate(raw.splitlines(keepends=False), 1):
        s = pl.strip()
        if not s: continue
        try:
            rows.append(json.loads(s, strict=False))
        except Exception as e:
            errs.append((lnno, str(e), s[:80]))
    return rows, errs


def sim(a, b):
    a, b = (a or "").strip(), (b or "").strip()
    if not a and not b: return 1.0
    if not a or not b: return 0.0
    return SequenceMatcher(None, a, b).ratio()


def same_company(c1, c2):
    """核心公司词匹配+相似度，兼容腾讯/字节不同写法"""
    c1, c2 = (c1 or "").strip(), (c2 or "").strip()
    if not c1 or not c2: return False
    # 核心公司识别：腾讯
    is_t1 = any(k in c1 for k in ["腾讯", "Tencent", "tencent"])
    is_t2 = any(k in c2 for k in ["腾讯", "Tencent", "tencent"])
    if is_t1 and is_t2: return True
    # 核心公司识别：字节
    is_b1 = any(k in c1 for k in ["字节", "ByteDance", "bytedance", "豆包", "抖音", "火山"])
    is_b2 = any(k in c2 for k in ["字节", "ByteDance", "bytedance", "豆包", "抖音", "火山"])
    if is_b1 and is_b2: return True
    # 其他：字符串相似度 >=0.9 视为同公司
    return sim(c1, c2) >= 0.90


def meta(r, label):
    return {
        "src": label,
        "source_id": r.get("source_id"),
        "source_company": r.get("source_company") or r.get("company_name"),
        "job_title_raw": r.get("job_title_raw"),
    }


def judge(rA, rB):
    """返回 (level, scores) 或 (None, None)"""
    comp_A = rA.get("source_company") or rA.get("company_name") or ""
    comp_B = rB.get("source_company") or rB.get("company_name") or ""
    title_A = rA.get("job_title_raw") or ""
    title_B = rB.get("job_title_raw") or ""
    resp_A = (rA.get("responsibilities") or "")[:1000]
    resp_B = (rB.get("responsibilities") or "")[:1000]
    company_sim = sim(comp_A, comp_B)
    title_sim = sim(title_A, title_B)
    resp_sim = sim(resp_A, resp_B)
    same_c = same_company(comp_A, comp_B)
    scores = {"company_sim": round(company_sim, 3), "title_sim": round(title_sim, 3),
              "resp_sim": round(resp_sim, 3), "same_company_rule": same_c}
    # STRONG
    if same_c and title_sim >= 0.75 and resp_sim >= 0.65:
        return "STRONG", scores
    # WEAK
    weak = (same_c and title_sim >= 0.60) or (title_sim >= 0.80) or (resp_sim >= 0.80)
    if weak:
        return "WEAK", scores
    return None, None


def main():
    off, _ = read_jsonl(OFFICIAL_50)
    zhi, _ = read_jsonl(ZHILIAN)
    gol, _ = read_jsonl(GOLD110)
    print(f"[1] 数据集加载: 官网50={len(off)} 智联candidate={len(zhi)} Gold110={len(gol)}")
    if len(off) != 50:
        print("[WARN] 官网50不是50条，跨源结果可能不准")
    results = []
    strong_cnt = weak_cnt = 0
    # 比较对：官网 vs 智联
    pairs = []
    for i, o in enumerate(off):
        for j, z in enumerate(zhi):
            lvl, sc = judge(o, z)
            if lvl:
                pairs.append((lvl, sc, meta(o, "official_50"), meta(z, "zhilian_candidate")))
    # 比较对：官网 vs Gold
    for i, o in enumerate(off):
        for j, g in enumerate(gol):
            lvl, sc = judge(o, g)
            if lvl:
                pairs.append((lvl, sc, meta(o, "official_50"), meta(g, "gold_110")))
    # 按STRONG先输出
    pairs.sort(key=lambda x: 0 if x[0] == "STRONG" else 1)
    for lvl, sc, m1, m2 in pairs:
        if lvl == "STRONG": strong_cnt += 1
        else: weak_cnt += 1
        results.append({"level": lvl, "scores": sc, "pair": [m1, m2]})
    # 写 JSONL（逐行，标准格式）
    with open(OUT_JSONL, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False))
            fh.write("\n")
    print(f"\n[2] 跨源匹配总计: STRONG={strong_cnt}  WEAK={weak_cnt} （只报告，不删除任何数据）")
    print(f"[3] 详细记录: {OUT_JSONL.name}（共{len(results)}条）")
    print(f"\n前10条摘录:")
    for r in results[:10]:
        print(f"  [{r['level']}] {r['pair'][0]['job_title_raw'][:22]}({r['pair'][0]['source_company'][:8]}) ↔ {r['pair'][1]['job_title_raw'][:22]}({r['pair'][1]['src']}) scores={r['scores']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
