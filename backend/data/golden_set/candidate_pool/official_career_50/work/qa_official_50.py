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
from datetime import datetime, timezone, timedelta
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


# ---------- §二 canonical 共享兼容函数 ----------
def is_tencent(sc: str) -> bool:
    sc = (sc or "").strip()
    return bool(sc) and ("Tencent" in sc or "腾讯" in sc)


def is_bytedance(sc: str) -> bool:
    sc = (sc or "").strip()
    return bool(sc) and ("ByteDance" in sc or "字节" in sc or "北京字节跳动网络技术有限公司" in sc)


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
    t = sum(1 for r in rows if is_tencent(r.get("source_company") or ""))
    b = sum(1 for r in rows if is_bytedance(r.get("source_company") or ""))
    return t, b


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

    # ========== §七 防回归 七条硬规则 ==========
    print("\n========== §七 防回归检查 ==========")
    issues_regression = []
    # 1. source_company 允许值仅：Tencent / ByteDance （RAW & CLEAN）
    ALLOWED_SC = {"Tencent", "ByteDance"}
    for lbl, rows in (("RAW", raw_rows), ("CLEAN", clean_rows)):
        bad = [(i+1, repr(r.get("source_company")), r.get("source_id"))
               for i, r in enumerate(rows) if r.get("source_company") not in ALLOWED_SC]
        if bad:
            issues_regression.append(f"{lbl} source_company 含非法值: {len(bad)} 条（首5: {bad[:5]}）")
    # 2. 总量 clean T=25 / B=25
    ct_t = sum(1 for r in clean_rows if is_tencent(r.get("source_company", "")))
    ct_b = sum(1 for r in clean_rows if is_bytedance(r.get("source_company", "")))
    if ct_t != 25 or ct_b != 25:
        issues_regression.append(f"clean 总量 T/B != 25/25（实际 {ct_t}/{ct_b}）")
    # 3. Pilot20 前20条 T=10 / B=10
    pt_t = sum(1 for r in clean_rows[:20] if is_tencent(r.get("source_company", "")))
    pt_b = sum(1 for r in clean_rows[:20] if is_bytedance(r.get("source_company", "")))
    if pt_t != 10 or pt_b != 10:
        issues_regression.append(f"Pilot20 分段 T/B != 10/10（实际 {pt_t}/{pt_b}）")
    # 4. 新增后30条 T=15 / B=15
    nt_t = sum(1 for r in clean_rows[20:] if is_tencent(r.get("source_company", "")))
    nt_b = sum(1 for r in clean_rows[20:] if is_bytedance(r.get("source_company", "")))
    if nt_t != 15 or nt_b != 15:
        issues_regression.append(f"新增30 分段 T/B != 15/15（实际 {nt_t}/{nt_b}）")
    # 5. README / distribution_report 关键词黑名单（§七 防止口径/分布/临时文件说明的回归）
    import re as _re
    def contains_outside(text, pat, exclude_pats):
        """text含pat且不落在exclude_pats任一场景内才返回True"""
        m = _re.search(pat, text)
        if not m: return False
        for ep in exclude_pats:
            if _re.search(ep, text): return False
        return True
    # 5a. README：本目录自称「黄金集」→禁止；但"Gold 110 条正式黄金集"等对Gold的引用是正确的；对work里已删除临时存档 checkpoint×4/inventory/batch_d候选池 →禁止在文件描述列表写为"仍保留"；但"已全部移除"上下文正确
    rm_path = ROOT / "README.md"
    if rm_path.exists():
        txt = rm_path.read_text(encoding="utf-8")
        # 误称：本目录自称 50 条黄金集；但对 Gold110 的定位描述「尚未进入 official Gold 110 条正式黄金集」是正确的 → 豁免
        def find_huangjinji_errors(doc: str):
            bad = []
            for m in _re.finditer(r"(?:企业?官网)\s*50\s*条(?:正式)?黄金集|50条(?:正式)?黄金集|目录.*黄金集|本(?:50条)?(?:正式)?黄金集", doc):
                s = m.start(); e = m.end()
                left = max(0, s-80); right = min(len(doc), e+80)
                ctx = doc[left:right]
                if "尚未进入" in ctx or "Gold 110" in ctx or "Gold110" in ctx or "candidate_pool" in ctx.lower():
                    continue  # 定位语境内：这是对Gold 110的说明，不是本目录自称
                bad.append((m.group(), s))
            return bad
        hj = find_huangjinji_errors(txt)
        if hj:
            issues_regression.append(f"README 误称口径：本目录自称「黄金集」（首2处: {hj[:2]}）；应改为「正式候选数据集」")
        # 误分布：T15+B35 型总表合计
        if _re.search(r"(合计|最终)[^\n]{0,20}(腾讯|Tencent)[^\n0-9]{0,6}15[^\n]{0,20}(字节|ByteDance)[^\n0-9]{0,6}35", txt):
            issues_regression.append("README 误分布：合计写成 腾讯15 + 字节35（应为25/25）")
        if _re.search(r"Tencent\s*\*\*15\*\*\s*\+\s*ByteDance\s*\*\*35\*\*", txt):
            issues_regression.append("README 误分布：Tencent 15 + ByteDance 35（应为25/25）")
        # Pilot20 腾讯/Tencent =0
        if _re.search(r"Pilot20.{0,160}(腾讯|Tencent).{0,12}(?:=|等于|:)\s*0(?!\d)", txt):
            issues_regression.append("README 统计回归：Pilot20 腾讯/Tencent =0（应为10）")
        # 误写：README把已删除临时文件 checkpoint×4 / inventory / batch_d候选池 列在work目录的保留清单语境下
        # 正确语境：含"已全部移除 / 已删除 / 清理后 / 移除不保留"等说明
        def bad_deleted_listed(doc: str, pat: str):
            for m in _re.finditer(pat, doc):
                ln_start = doc.rfind("\n", 0, m.start()) + 1
                ln_end = doc.find("\n", m.end())
                if ln_end < 0: ln_end = len(doc)
                line = doc[ln_start:ln_end]
                if any(k in line for k in ["移除", "已删除", "清理后", "不保留", "移去"]):
                    continue
                return True, line[:120]
            return False, ""
        for err_pat, reason in [
            (r"checkpoint\s*[×x*]\s*4\s*/\s*inventory\s*/\s*batch_d", 'work/仍写 "checkpoint ×4 / inventory / batch_d 候选池" 在保留列表'),
            (r"中间工作目录[^\n]*checkpoint\s*[×x*]\s*4", 'work/仍写 "中间工作目录 checkpoint×4"'),
        ]:
            found, snippet = bad_deleted_listed(txt, err_pat)
            if found:
                issues_regression.append(f"README work目录说明回归：{reason} → 片段{snippet!r}")
    # 5b. distribution_report：阶段表数字错误（0/20、合计15/35）——使用表头匹配（紧跟标题行|阶段|...)
    dist_path = ROOT / "official_career_50_distribution_report.md"
    if dist_path.exists():
        dtxt = dist_path.read_text(encoding="utf-8")
        if _re.search(r"\|\s*Pilot20\s*\|\s*20\s*\|\s*0\s*\|\s*20\s*\|", dtxt):
            issues_regression.append("distribution 阶段表回归：Pilot20 写成 腾讯=0/字节=20（应为10/10）")
        if _re.search(r"\|\s*合计\s*\|\s*50\s*\|\s*15\s*\|\s*35\s*\|", dtxt):
            issues_regression.append("distribution 阶段表回归：合计写成 腾讯15/字节35（应为25/25）")
    # 6. publish_time 未来异常：显式报告（不得静默 PASS；允许异常存在但必须在quality_report中标注）
    future_anoms = []
    for i, r in enumerate(clean_rows, 1):
        pts, cts = r.get("publish_time"), r.get("crawl_time")
        if not pts or not cts: continue
        try:
            pd = datetime.fromisoformat(str(pts).replace("Z", "+00:00"))
            cd = datetime.fromisoformat(str(cts).replace("Z", "+00:00"))
            if pd.tzinfo is None: pd = pd.replace(tzinfo=timezone.utc)
            if cd.tzinfo is None: cd = cd.replace(tzinfo=timezone.utc)
            if pd > cd:
                future_anoms.append({"line": i, "source_id": r.get("source_id"),
                                     "publish_time": pts, "crawl_time": cts})
        except Exception:
            pass
    print(f"  publish_time 未来异常数: {len(future_anoms)} 条")
    for a in future_anoms:
        print(f"    ⚠️ L{a['line']} sid={a['source_id']} publish={a['publish_time']} crawl={a['crawl_time']}")
    # 质量报告必须显式标注（若文件已写，预扫描关键词）：不阻塞 exit，仅警告 + 通过最终报告
    qpath = ROOT / "official_career_50_quality_report.md"
    if future_anoms and qpath.exists():
        qtxt = qpath.read_text(encoding="utf-8")
        if "future publish_time anomaly" not in qtxt and "publish_time 未来异常" not in qtxt:
            issues_regression.append("quality_report 未显式标注 publish_time 未来异常（§七#6 不得静默PASS）")
    # 打印 §七 结论
    if issues_regression:
        for x in issues_regression:
            print(f"  ❌ REGRESSION_FAIL: {x}")
    else:
        print("  ✅ §七 防回归 全部 PASS")

    final_report = {
        "qa_raw": qa_raw, "qa_clean": qa_clean,
        "pilot20_six_check": {"pass": pilot_ok, "failed_count": len(pilot_fails)},
        "regression": {
            "issues": issues_regression,
            "segments": {
                "clean_total": {"T": ct_t, "B": ct_b},
                "pilot20": {"T": pt_t, "B": pt_b},
                "new30": {"T": nt_t, "B": nt_b},
            },
            "publish_time_anomalies": {
                "future_count": len(future_anoms),
                "items": future_anoms,
            },
            "source_company_allowed_only": len(ALLOWED_SC) == 2 and ALLOWED_SC == {"Tencent", "ByteDance"},
        },
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
    if issues_regression:
        issues.extend(["REGRESSION: " + x for x in issues_regression])
    if issues:
        print("\n⚠️ REVIEW_REQUIRED: 存在以下QA问题:")
        for it in issues: print(f"  - {it}")
        sys.exit(1)
    else:
        print("\n🎉 §九+§十+§七 QA 全部 PASS ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
