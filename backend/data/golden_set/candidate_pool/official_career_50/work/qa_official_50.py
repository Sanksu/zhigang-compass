#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S5 §九+§十：正式50条完整QA + Pilot20六项不可变检查
位置：work/ 纯stdlib；
§九 QA清单：
  - raw记录=50，clean记录=50
  - Tencent=25，ByteDance=25（各记录）
  - source_id唯一 50/50
  - source_url唯一 50/50
  - 8必填字段(job_title_raw/company_name/location/responsibilities/requirements/detail_raw_text) 各 50/50
  - _sha256 格式合法 50/50（^[a-f0-9]{64}$）
  - 完全重复 = 0
  - 近似重复：逐条报告（sha256前缀8相同 或 title_jaccard>=0.7 或 resp前缀200字符jaccard>=0.8）
§十 Pilot20不可变：
  - 前20条六项与official_career_pilot20_clean字节级比较 20/20
  - 任何一项不一致 exit(1)
"""
import hashlib
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
PILOT20 = ROOT.parent / "official_career_pilot20" / "official_career_pilot20_clean.jsonl"
RAW = ROOT / "official_career_50_raw.jsonl"
CLEAN = ROOT / "official_career_50_clean.jsonl"
REPORT = WORK / "qa_official_50_report.json"  # 存档QA结果供后续报告读取
SIX = ("source_id", "source_url", "responsibilities", "requirements", "detail_raw_text", "_sha256")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
REQ_FIELDS = ("source_id", "source_url", "job_title_raw", "company_name", "location",
              "responsibilities", "requirements", "detail_raw_text", "_sha256")


# ---------- 读取 ----------
def read_jsonl(p):
    rows, errs = [], []
    if not p.exists(): return rows, errs
    with open(p, "r", encoding="utf-8") as fh:
        raw = fh.read()
    phys = raw.splitlines(keepends=False)
    for lnno, pl in enumerate(phys, 1):
        s = pl.strip()
        if not s: continue
        if "}\\n{" in s:
            chs = s.split("}\\n{")
            segs = []
            for i, c in enumerate(chs):
                if i == 0: seg = c + "}"
                elif i == len(chs)-1: seg = "{" + c
                else: seg = "{" + c + "}"
                seg = seg.strip()
                while seg.startswith("\\n"): seg = seg[2:].strip()
                while seg.endswith("\\n"): seg = seg[:-2].strip()
                if seg: segs.append(seg)
        else:
            s2 = s.strip()
            while s2.startswith("\\n"): s2 = s2[2:].strip()
            while s2.endswith("\\n"): s2 = s2[:-2].strip()
            segs = [s2] if s2 else []
        for si, sg in enumerate(segs):
            try: rows.append(json.loads(sg, strict=False))
            except Exception as e: errs.append((lnno, si, str(e), sg[:80]))
    return rows, errs


def tb_count(rows):
    t = sum(1 for r in rows if "腾讯" in (r.get("source_company") or "") or "Tencent" in (r.get("source_company") or ""))
    return t, len(rows)-t


# ---------- §九 QA ----------
def qa(rows, label):
    out = {"label": label}
    N = len(rows)
    out["count"] = N
    print(f"\n========== QA[{label}] ==========")
    print(f"  总行数: {N} (期望50)")
    t, b = tb_count(rows)
    out["t_count"], out["b_count"] = t, b
    print(f"  T={t} B={b} (期望25/25)")
    # 唯一性
    sids = [r.get("source_id") for r in rows]
    urls = [r.get("source_url") for r in rows]
    out["sid_unique"], out["url_unique"] = len(set(sids)), len(set(urls))
    print(f"  source_id唯一: {out['sid_unique']}/{N} (期望{N})")
    print(f"  source_url唯一: {out['url_unique']}/{N} (期望{N})")
    # 必填字段完整率
    out["fields"] = {}
    for f in REQ_FIELDS:
        ok = sum(1 for r in rows if r.get(f) and isinstance(r.get(f), str) and len(r.get(f).strip()) > 0)
        out["fields"][f] = {"ok": ok, "total": N, "rate": f"{ok}/{N}"}
        print(f"  {f}: {ok}/{N}")
    # sha256格式
    sha_ok = sum(1 for r in rows if SHA256_RE.match(r.get("_sha256", "") or ""))
    out["sha256_format_ok"] = sha_ok
    print(f"  _sha256格式合法(^[a-f0-9]{{64}}$): {sha_ok}/{N}")
    # 完全重复（整行JSON序列化完全相同）
    sigs = [json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows]
    full_dup = len(sigs) - len(set(sigs))
    out["full_dup_count"] = full_dup
    print(f"  完全重复(整行一致): {full_dup} (期望0)")
    # 近似重复
    approx = []
    def sim(a, b):
        if not a or not b: return 0.0
        return SequenceMatcher(None, a, b).ratio()
    for i in range(N):
        for j in range(i+1, N):
            ri, rj = rows[i], rows[j]
            reasons = []
            sha_i, sha_j = ri.get("_sha256", ""), rj.get("_sha256", "")
            if len(sha_i) >= 8 and sha_i[:8] == sha_j[:8]:
                reasons.append(f"sha256前缀8相同={sha_i[:8]}")
            title_sim = sim(ri.get("job_title_raw", ""), rj.get("job_title_raw", ""))
            if title_sim >= 0.70:
                reasons.append(f"title相似度={title_sim:.2f}")
            resp_i, resp_j = (ri.get("responsibilities") or "")[:250], (rj.get("responsibilities") or "")[:250]
            resp_sim = sim(resp_i, resp_j)
            if resp_sim >= 0.80:
                reasons.append(f"resp前250相似度={resp_sim:.2f}")
            if reasons:
                approx.append({
                    "i": i+1, "j": j+1,
                    "sid_i": ri.get("source_id"), "sid_j": rj.get("source_id"),
                    "title_i": ri.get("job_title_raw"), "title_j": rj.get("job_title_raw"),
                    "reasons": reasons,
                })
    out["approx_dup"] = approx
    print(f"  近似重复候选: {len(approx)} 对（详见report）")
    for ap in approx[:10]:
        print(f"    行{ap['i']}({ap['title_i'][:18]}) ↔ 行{ap['j']}({ap['title_j'][:18]}): {ap['reasons']}")
    return out


# ---------- §十 Pilot20 不可变 ----------
def pilot_check(clean_rows, pilot_rows):
    print(f"\n========== §十 Pilot20六项不可变检查 ==========")
    print(f"  Pilot20原文: {len(pilot_rows)} 条 (期望20)")
    print(f"  正式50前20条 (clean): 前20条已加载")
    failed = []
    for i in range(20):
        pr = pilot_rows[i]
        cr = clean_rows[i]
        for f in SIX:
            pv, cv = pr.get(f), cr.get(f)
            if pv != cv:
                failed.append((i+1, f, type(pv).__name__, type(cv).__name__,
                               (str(pv)[:80] + "…") if len(str(pv))>80 else str(pv),
                               (str(cv)[:80] + "…") if len(str(cv))>80 else str(cv)))
    if failed:
        print("  ❌ REVIEW_REQUIRED: Pilot20六项保护失败，共", len(failed), "处不一致")
        for (ln, f, tp, tc, pv, cv) in failed[:20]:
            print(f"    L{ln} field={f} pilot_type={tp} clean_type={tc}")
            print(f"      pilot= {pv}")
            print(f"      clean= {cv}")
        return False, failed
    print("  ✅ PASS Pilot20六项保护 20/20 字节级完全一致")
    return True, []


def main():
    pilot_rows, _ = read_jsonl(PILOT20)
    raw_rows, _ = read_jsonl(RAW)
    clean_rows, _ = read_jsonl(CLEAN)
    qa_raw = qa(raw_rows, "RAW")
    qa_clean = qa(clean_rows, "CLEAN")
    pilot_ok, pilot_fails = pilot_check(clean_rows, pilot_rows)
    final_report = {
        "qa_raw": qa_raw, "qa_clean": qa_clean,
        "pilot20_six_check": {"pass": pilot_ok, "failed_count": len(pilot_fails)},
    }
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(final_report, f, ensure_ascii=False, indent=2)
    print(f"\nQA报告存档: {REPORT}")
    # 汇总校验
    issues = []
    for lbl, q in (("RAW", qa_raw), ("CLEAN", qa_clean)):
        if q["count"] != 50: issues.append(f"{lbl} count!=50")
        if q["t_count"] != 25 or q["b_count"] != 25: issues.append(f"{lbl} T/B!=25/25")
        if q["sid_unique"] != 50: issues.append(f"{lbl} sid_unique!=50")
        if q["url_unique"] != 50: issues.append(f"{lbl} url_unique!=50")
        if q["sha256_format_ok"] != 50: issues.append(f"{lbl} sha256!=50")
        if q["full_dup_count"] != 0: issues.append(f"{lbl} full_dup>0")
        for f, info in q["fields"].items():
            if info["ok"] != 50: issues.append(f"{lbl} 必填字段{f}不完整 {info['rate']}")
    if not pilot_ok:
        issues.append("Pilot20六项保护失败")
    if issues:
        print("\n⚠️ REVIEW_REQUIRED: 存在以下QA问题:")
        for it in issues: print(f"  - {it}")
        sys.exit(1)
    else:
        print("\n🎉 §九+§十 QA 全部 PASS ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
