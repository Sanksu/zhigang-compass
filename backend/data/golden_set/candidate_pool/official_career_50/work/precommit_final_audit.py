#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S0-S4 终审只读脚本：不做任何写/删；输出L24/L25逐字段人工对比+全量近似重复+Pilot20六项终审+正式QA终审
位置：work/ 纯stdlib
"""
import hashlib
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
PILOT = ROOT.parent / "official_career_pilot20" / "official_career_pilot20_clean.jsonl"
RAW = ROOT / "official_career_50_raw.jsonl"
CLEAN = ROOT / "official_career_50_clean.jsonl"
SAVE = WORK / "precommit_audit_result.json"
SHA_RE = re.compile(r"^[a-f0-9]{64}$")
REQ_F = ["source_id", "source_url", "job_title_raw", "company_name", "location",
         "responsibilities", "requirements", "detail_raw_text", "_sha256"]
SIX = ["source_id", "source_url", "responsibilities", "requirements", "detail_raw_text", "_sha256"]


def read_jsonl(p):
    rows = []
    if not p.exists(): return rows
    with open(p, "r", encoding="utf-8") as fh: raw = fh.read()
    for pl in raw.splitlines(keepends=False):
        s = pl.strip()
        if not s: continue
        try: rows.append(json.loads(s, strict=False))
        except: pass
    return rows


def sim(a, b):
    a, b = (a or "").strip(), (b or "").strip()
    if not a and not b: return 1.0
    if not a or not b: return 0.0
    return round(SequenceMatcher(None, a, b).ratio(), 4)


def main():
    out = {}
    clean = read_jsonl(CLEAN)
    raw = read_jsonl(RAW)
    pilot = read_jsonl(PILOT)
    N = len(clean); NR = len(raw)
    print(f"[LOAD] clean={N} raw={NR} pilot={len(pilot)}")
    # ======== 一、L24/L25 人工复核（索引23、24，1-based行号L24=第24条） ========
    a = clean[23]; b = clean[24]
    fields_9 = ["source_company", "source_id", "source_url", "location", "responsibilities",
                "requirements", "detail_raw_text", "_sha256"]
    section1 = {"L24_order": a.get("_rid"), "L24_title": a.get("job_title_raw"),
                "L25_order": b.get("_rid"), "L25_title": b.get("job_title_raw")}
    section1["fields"] = {}
    diff_count = 0
    for f in fields_9:
        av, bv = a.get(f), b.get(f)
        eq = (av == bv)
        if not eq: diff_count += 1
        s = sim(av, bv) if isinstance(av, str) and isinstance(bv, str) else (1.0 if eq else 0.0)
        section1["fields"][f] = {"equal": eq, "sim": s,
            "L24": av[:200] + ("…" if isinstance(av, str) and len(av) > 200 else "") if isinstance(av, str) else av,
            "L25": bv[:200] + ("…" if isinstance(bv, str) and len(bv) > 200 else "") if isinstance(bv, str) else bv}
    # 正文相似度拆分计算
    resp_sim = sim(a.get("responsibilities"), b.get("responsibilities"))
    req_sim = sim(a.get("requirements"), b.get("requirements"))
    d_raw_sim = sim(a.get("detail_raw_text"), b.get("detail_raw_text"))
    # 职责Jaccard（句子/条目的字符重叠-更细）
    section1["sims"] = {"responsibilities_sim": resp_sim, "requirements_sim": req_sim, "detail_raw_text_sim": d_raw_sim}
    section1["distinct_non_equal_fields_count"] = diff_count
    print("\n====== 一、L24/L25 人工复核（9项逐字段）======")
    print(f"  L24 [{a.get('_rid')}] {a.get('job_title_raw')}")
    print(f"  L25 [{b.get('_rid')}] {b.get('job_title_raw')}")
    for f in fields_9:
        info = section1["fields"][f]
        mark = "✅一致" if info["equal"] else f"❌不同 (sim={info['sim']:.3f})"
        print(f"  · {f:20s} {mark}")
        if not info["equal"]:
            print(f"     L24={info['L24'][:80]}")
            print(f"     L25={info['L25'][:80]}")
    print(f"  正文resp相似度={resp_sim:.4f} req相似度={req_sim:.4f} detail_raw相似度={d_raw_sim:.4f}")
    print(f"  不同字段总数（8个正文类+sha）= {diff_count}/8 → 人工判定基准：<3且sha相同=DUPLICATE；≥3或resp/req sim<0.85=DISTINCT")
    out["L24_L25_audit"] = section1

    # ======== 二、重扫全部完全重复/近似重复 ========
    print("\n====== 二、全量重复扫描（50×50，CLEAN）======")
    sigs = [json.dumps(r, sort_keys=True, ensure_ascii=False) for r in clean]
    full_dup = len(sigs) - len(set(sigs))
    approx = []
    for i in range(N):
        ri = clean[i]
        for j in range(i+1, N):
            rj = clean[j]
            reasons = []
            sha_i, sha_j = ri.get("_sha256", ""), rj.get("_sha256", "")
            if len(sha_i) >= 8 and sha_i[:8] == sha_j[:8]: reasons.append(f"sha前缀8同={sha_i[:8]}")
            t_sim = sim(ri.get("job_title_raw", ""), rj.get("job_title_raw", ""))
            if t_sim >= 0.70: reasons.append(f"title_sim={t_sim:.3f}")
            r_sim = sim((ri.get("responsibilities") or "")[:300], (rj.get("responsibilities") or "")[:300])
            if r_sim >= 0.80: reasons.append(f"resp300_sim={r_sim:.3f}")
            if reasons:
                approx.append({"i": i+1, "j": j+1, "rid_i": ri.get("_rid"), "rid_j": rj.get("_rid"),
                               "t_i": ri.get("job_title_raw")[:30], "t_j": rj.get("job_title_raw")[:30],
                               "reasons": reasons, "company_match": (ri.get("source_company") == rj.get("source_company"))})
    print(f"  完全重复整行一致 = {full_dup}（期望0）")
    print(f"  近似重复候选 = {len(approx)} 对")
    if approx:
        for ap in sorted(approx, key=lambda x: -sum(1 for r in x["reasons"] if 'resp300_sim' in r or 'sha前缀8同' in r)):
            print(f"    L{ap['i']}({ap['t_i']}) ↔ L{ap['j']}({ap['t_j']}) company_eq={ap['company_match']} reasons={'; '.join(ap['reasons'])}")
    else:
        print(f"    其他近似重复候选=0（只有本次重点L24/L25组列入）")
    out["global_dup_scan"] = {"full_dup_count": full_dup, "approx_dup_pairs": approx, "approx_dup_count": len(approx)}

    # ======== 三、Pilot20六项终审 ========
    print("\n====== 三、Pilot20六项终审（50.clean前20 vs 原pilot.clean）======")
    fails = []
    for i in range(20):
        pr = pilot[i]; cr = clean[i]
        for f in SIX:
            pv, cv = pr.get(f), cr.get(f)
            if pv != cv:
                fails.append((i+1, f, (str(pv)[:80] + "…" if len(str(pv))>80 else pv),
                                           (str(cv)[:80] + "…" if len(str(cv))>80 else cv)))
    if fails:
        print(f"  ❌ REVIEW_REQUIRED 共 {len(fails)} 处不一致：")
        for ln, f, pv, cv in fails[:20]: print(f"    L{ln} field={f}")
    else:
        print("  ✅ PASS 六项 20/20 字节级完全一致")
    out["pilot20_six_final"] = {"pass": len(fails) == 0, "failed_count": len(fails), "fail_list": fails[:20]}

    # ======== 四、正式数据最终QA ========
    print("\n====== 四、正式数据最终QA（程序实际重新读取 raw/clean）======")
    t_c = sum(1 for r in clean if "腾讯" in (r.get("source_company") or "") or "Tencent" in (r.get("source_company") or ""))
    t_r = sum(1 for r in raw if "腾讯" in (r.get("source_company") or "") or "Tencent" in (r.get("source_company") or ""))
    qa = {}
    print(f"  raw行数 = {NR} （期望50）")
    print(f"  clean行数 = {N} （期望50）")
    print(f"  clean Tencent={t_c}（期望25） ByteDance={N-t_c}（期望25）")
    print(f"  raw   Tencent={t_r}（期望25） ByteDance={NR-t_r}（期望25）")
    qa.update({"raw_n": NR, "clean_n": N, "t_clean": t_c, "b_clean": N-t_c, "t_raw": t_r, "b_raw": NR-t_r})
    # 唯一性
    u_sid_c = len({r["source_id"] for r in clean if r.get("source_id")})
    u_url_c = len({r["source_url"] for r in clean if r.get("source_url")})
    u_sid_r = len({r["source_id"] for r in raw if r.get("source_id")})
    u_url_r = len({r["source_url"] for r in raw if r.get("source_url")})
    print(f"  CLEAN source_id唯一 = {u_sid_c}/50")
    print(f"  CLEAN source_url唯一 = {u_url_c}/50")
    print(f"  RAW source_id唯一 = {u_sid_r}/50")
    print(f"  RAW source_url唯一 = {u_url_r}/50")
    qa.update({"u_sid_c": u_sid_c, "u_url_c": u_url_c, "u_sid_r": u_sid_r, "u_url_r": u_url_r})
    # 必填字段 9 项
    print(f"\n  CLEAN 必填字段 50/50？")
    ok = {}
    for f in REQ_F:
        c_ok = sum(1 for r in clean if r.get(f) and isinstance(r.get(f), str) and r.get(f).strip())
        ok[f] = c_ok
        ok_flag = "✅" if c_ok == 50 else "❌"
        print(f"    {f:25s} {c_ok}/50 {ok_flag}")
    sha_ok_c = sum(1 for r in clean if SHA_RE.match(r.get("_sha256", "") or ""))
    sha_ok_r = sum(1 for r in raw if SHA_RE.match(r.get("_sha256", "") or ""))
    print(f"    _sha256格式合法 64hex       CLEAN={sha_ok_c}/50 {'✅' if sha_ok_c==50 else '❌'}   RAW={sha_ok_r}/50 {'✅' if sha_ok_r==50 else '❌'}")
    qa["fields_ok_clean"] = ok
    qa["sha_clean"] = sha_ok_c
    qa["sha_raw"] = sha_ok_r
    # PASS 判定
    qa_pass = (NR == 50 and N == 50 and t_c == 25 and (N-t_c) == 25 and t_r == 25 and (NR-t_r) == 25
               and u_sid_c == 50 and u_url_c == 50 and u_sid_r == 50 and u_url_r == 50
               and all(v == 50 for v in ok.values()) and sha_ok_c == 50 and sha_ok_r == 50)
    qa["ALL_PASS"] = qa_pass
    print(f"\n  → QA 终审 {'✅ PASS' if qa_pass else '❌ FAIL'}")
    out["final_qa"] = qa
    # 存档
    with open(SAVE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n[√] 审计结果存档: {SAVE.name}")
    return 0 if qa_pass and len(fails) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
