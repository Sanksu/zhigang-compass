#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S4 §八：Python文件流生成正式50条 raw/clean（20 Pilot + 30 新增 = 50；T25 + B25）
位置：work/ 纯 stdlib；禁止聊天大文本Write；所有I/O走open()+json逐行读写；写后重读验证count=50/50
规则：
  - Pilot20 20条：100%字节级继承所有字段（包括_sha256），绝不重算不重写
  - 新增30条：sid/url唯一，与Pilot20 0重叠；_sha256用 >>>0 无符号标准SHA256 64位十六进制重算（输入=resp+"\\n"+req）
  - 写 official_career_50_raw.jsonl / official_career_50_clean.jsonl → 放在 official_career_50/ 根目录（不再work/）
  - 写后立刻脚本逐行读回验证，任何count!=50 → exit(1) REVIEW_REQUIRED
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

SHA256_RE = re.compile(r"^[a-f0-9]{64}$")

ROOT = Path(__file__).resolve().parent.parent  # official_career_50/
WORK = ROOT / "work"
PILOT20_CLEAN = ROOT.parent / "official_career_pilot20" / "official_career_pilot20_clean.jsonl"
OFFICIAL_RAW = ROOT / "official_career_50_raw.jsonl"
OFFICIAL_CLEAN = ROOT / "official_career_50_clean.jsonl"
CK_FILES = [
    WORK / "batch_a_checkpoint.jsonl",
    WORK / "batch_b_checkpoint.jsonl",
    WORK / "batch_c_checkpoint.jsonl",
    WORK / "batch_d_checkpoint.jsonl",  # S3新增 13条字节
]
PILOT_SIX_FIELDS = ("source_id", "source_url", "responsibilities", "requirements", "detail_raw_text", "_sha256")


# ========== §一 canonical source_company 共享兼容函数 ==========
def is_tencent(sc: str) -> bool:
    sc = (sc or "").strip()
    return bool(sc) and ("Tencent" in sc or "腾讯" in sc)


def is_bytedance(sc: str) -> bool:
    sc = (sc or "").strip()
    return bool(sc) and ("ByteDance" in sc or "字节" in sc or "北京字节跳动网络技术有限公司" in sc)


def canonical_source_company(sc: str) -> str:
    """新增30条 source_company 统一输出 canonical 英文值；
    Pilot20 继承不走此函数。"""
    if is_tencent(sc):
        return "Tencent"
    if is_bytedance(sc):
        return "ByteDance"
    return (sc or "").strip()


# ========== 兼容三类JSONL读取 ==========
def read_jsonl(path: Path):
    """兼容：1)标准real-newline JSONL；2)伪JSONL(同1物理行字面量'\\n'拼接)；3)单行单对象；
    所有内部parse走 strict=False，容忍JD正文换行"""
    rows = []
    if not path.exists():
        return rows, []
    errs = []
    with open(path, "r", encoding="utf-8") as fh:
        s_raw = fh.read()
    # 先按真实换行分物理行，逐行处理（若物理行内含字面量拼接再二次拆分）
    phys_lines = s_raw.splitlines(keepends=False)
    for lineno, pline in enumerate(phys_lines, 1):
        s = pline.strip()
        if not s:
            continue
        if "}\\n{" in s:  # 字面量反斜杠+n 分隔
            chunks = s.split("}\\n{")
            segments = []
            for i, ch in enumerate(chunks):
                if i == 0: seg = ch + "}"
                elif i == len(chunks)-1: seg = "{" + ch
                else: seg = "{" + ch + "}"
                seg = seg.strip()
                while seg.startswith("\\n"): seg = seg[2:].strip()
                while seg.endswith("\\n"): seg = seg[:-2].strip()
                if seg: segments.append(seg)
        else:
            s2 = s.strip()
            while s2.startswith("\\n"): s2 = s2[2:].strip()
            while s2.endswith("\\n"): s2 = s2[:-2].strip()
            segments = [s2] if s2 else []
        for si, seg in enumerate(segments):
            try:
                rows.append(json.loads(seg, strict=False))
            except Exception as e:
                errs.append((lineno, f"seg{si}: {e}", seg[:120]))
    return rows, errs


def write_jsonl(path: Path, rows):
    """§一 原子写：标准JSONL（每行1对象，json.dumps自动转义换行→严格格式合规）"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False))
            fh.write("\n")
    os.replace(tmp, path)
    return True


# ========== SHA256 无符号64位十六进制 ==========
def calc_sha256(responsibilities: str, requirements: str) -> str:
    """§八/§九要求：输入 = responsibilities + '\\n' + requirements；
    输出：64位小写十六进制 ^[a-f0-9]{64}$，无负号（hexdigest天然无符号）"""
    payload = (responsibilities or "") + "\n" + (requirements or "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest().lower()


# ========== 新增条目 cleanify（对齐Pilot20 clean 24字段） ==========
def cleanify_new(r: dict, order_global: int) -> dict:
    """正文零改动；只补元字段(_direction/_rid)；clean结构24字段严格对齐Pilot20"""
    sc = r.get("source_company") or ""
    is_t = "腾讯" in sc or "Tencent" in sc
    raw_order = r.get("order", "")
    # 简单岗位族（仅填充_direction/_rid元字段，绝不改正文）
    title_cat = (r.get("job_title_raw") or "") + " " + (r.get("_category") or "")
    if any(k in title_cat for k in ["算法", "Algorithm", "研究员", "语音", "NLP", "风控", "多模态", "大模型", "混元", "机器学习", "Agent", "Cod"]):
        d = "算法"
    elif any(k in title_cat for k in ["后台", "后端", "服务端", "架构师", "SRE", "全栈", "BFF", "PUBG", "UGC", "协作工具", "Infra", "基础架构", "全球流量", "Kubernetes"]):
        d = "后端"
    elif any(k in title_cat for k in ["客户端", "SDK", "3A", "关卡", "战斗", "任务", "C++", "豆包", "引擎", "图形", "渲染"]):
        d = "客户端"
    elif any(k in title_cat for k in ["前端", "小程序", "React"]):
        d = "前端"
    elif any(k in title_cat for k in ["产品经理", "产品设计", "运营专家", "运营分析", "策略分析", "策划", "设计师"]):
        d = "产品/运营"  # batch_d中偏产品运营策划的岗位，如实归类
    elif any(k in title_cat for k in ["测试"]):
        d = "测试"
    elif any(k in title_cat for k in ["大数据", "数据工程", "数仓", "Flink", "Spark", "Hive", "数据质量", "评测"]):
        d = "大数据"
    elif any(k in title_cat for k in ["运维", "SRE", "可用性", "边缘"]):
        d = "运维"
    elif any(k in title_cat for k in ["安全", "逆向", "加密"]):
        d = "安全"
    elif any(k in title_cat for k in ["音视频", "秒剪"]):
        d = "音视频SDK"
    else:
        d = "其他"
    return {
        "source": r.get("source", "official_career_site"),
        "source_company": canonical_source_company(sc),  # §一 统一 canonical 英文值：Tencent / ByteDance
        "source_id": r["source_id"],
        "source_id_method": ("postId" if is_t else "URL_path_19_digit_job_id"),
        "source_url": r["source_url"],
        "job_title_raw": r["job_title_raw"],
        "company_name": r.get("company_name", ("腾讯科技（深圳）有限公司" if is_t else "北京字节跳动网络技术有限公司")),
        "location": r.get("location", ""),
        "salary": r.get("salary", None),
        "source_education": r.get("source_education", None),
        "source_experience": r.get("source_experience", None),
        "publish_time": r.get("publish_time", None),
        "responsibilities": r["responsibilities"],
        "requirements": r["requirements"],
        "detail_raw_text": r["detail_raw_text"],
        "crawl_time": r.get("crawl_time"),
        "_sha256": calc_sha256(r.get("responsibilities", ""), r.get("requirements", "")),  # 新增统一>>>0无符号重算
        "_direction": d,
        "_rid": f"NEW-{raw_order if raw_order else f'{order_global+1:02d}'}",
        "_simhash64": r.get("_simhash64", None),
        "_visit_status": "正常访问",
        "_display_position_id": None,
        "_job_category_raw": r.get("_category", None),
        "_adapter_parse_success": True,
    }


# ========== 主流程 ==========
def main():
    # 1. 读Pilot20 clean
    pilot_rows, pe = read_jsonl(PILOT20_CLEAN)
    assert len(pilot_rows) == 20, f"Pilot20 clean文件应为20条，实际={len(pilot_rows)}"
    print(f"[1] Pilot20 clean 读取: {len(pilot_rows)} 条 (20/20)")
    p_sids = {r["source_id"] for r in pilot_rows}
    p_urls = {r["source_url"] for r in pilot_rows}

    # 2. 读4个checkpoint（a/b/c/d = 7+5+5+13 = 30）；若checkpoint不存在（work清理后），fallback从当前OFFICIAL_RAW拆分：前20=Pilot20 后30=新增
    ck_all = []
    all_ck_exist = all(p.exists() for p in CK_FILES)
    if all_ck_exist:
        for p in CK_FILES:
            rows, errs = read_jsonl(p)
            if errs:
                print(f"  WARN {p.name}: parse错误 {len(errs)} 处")
            print(f"[2] {p.name}: {len(rows)} 条")
            ck_all.extend(rows)
        print(f"  [2] checkpoint总计原始记录: {len(ck_all)}")
    else:
        print("[2] checkpoint 临时文件已清理 → fallback 从 OFFICIAL_RAW 拆分：前20=Pilot20、后30=新增原始记录")
        raw_reload, _ = read_jsonl(OFFICIAL_RAW)
        assert len(raw_reload) == 50, f"fallback 需 OFFICIAL_RAW=50 条，实际={len(raw_reload)}"
        ck_all = list(raw_reload[20:])
        print(f"  [2] fallback后新增记录: {len(ck_all)} 条（期望30）")
        assert len(ck_all) == 30, f"fallback新增条数应=30，实际={len(ck_all)}"

    # 3. 去重唯一性 + 与Pilot20 0重叠验证
    seen_sid = set()
    seen_url = set()
    new_unique = []
    dup_sid, dup_url, dup_p_sid, dup_p_url = 0, 0, 0, 0
    for r in ck_all:
        sid = r.get("source_id"); url = r.get("source_url")
        if not sid or not url: continue
        if sid in seen_sid: dup_sid += 1; continue
        if url in seen_url: dup_url += 1; continue
        if sid in p_sids: dup_p_sid += 1; continue
        if url in p_urls: dup_p_url += 1; continue
        seen_sid.add(sid); seen_url.add(url); new_unique.append(r)
    print(f"[3] 新增去重唯一后: {len(new_unique)} 条（期望30）")
    print(f"    内部重复 sid={dup_sid} url={dup_url}；与Pilot20重叠 sid={dup_p_sid} url={dup_p_url}（都应=0）")
    assert len(new_unique) == 30, f"新增唯一记录应=30，实际={len(new_unique)}"
    t_count = sum(1 for r in new_unique if is_tencent(r.get("source_company") or ""))
    b_count = sum(1 for r in new_unique if is_bytedance(r.get("source_company") or ""))
    print(f"[3] Tencent新增={t_count}（期望15） ByteDance新增={b_count}（期望15）")
    assert t_count == 15 and b_count == 15, f"T/B新增分布不满足15/15，实际T={t_count} B={b_count}"

    # 4. 生成 RAW 50条：Pilot20原文（前20条字节级继承，含_sha256）+ 新增30条raw（补上_sha256 >>>0无符号，与clean新增的_sha256一致；因为QA要求RAW的_sha256也必须50/50）
    #    注意：此前 recovered_current_raw = Pilot20 clean list + 新增原始记录，因为 Pilot 20 没有单独 RAW 文件，clean就是唯一源（六项保护基于clean的）
    new_raw_30_with_sha = []
    for r in new_unique:
        r_copy = dict(r)
        # §一 RAW 的新增30条 source_company 同样必须 canonical（只改元字段，不改正文/sha输入）
        if "source_company" in r_copy:
            r_copy["source_company"] = canonical_source_company(r_copy.get("source_company") or "")
        if "_sha256" not in r_copy or not r_copy.get("_sha256") or not SHA256_RE.match(r_copy.get("_sha256", "")):
            r_copy["_sha256"] = calc_sha256(r_copy.get("responsibilities", ""), r_copy.get("requirements", ""))
        new_raw_30_with_sha.append(r_copy)
    raw_50 = list(pilot_rows) + new_raw_30_with_sha
    assert len(raw_50) == 50
    write_jsonl(OFFICIAL_RAW, raw_50)
    print(f"\n[4] raw 50写完成: {OFFICIAL_RAW.name}（新增30条已补_sha256，确保RAW的_sha256 50/50）")

    # 5. 生成 CLEAN 50条：Pilot20 clean原文 100%字节级继承（不重算_sha256） + 新增30条 cleanify + _sha256重算
    new_clean_30 = [cleanify_new(r, i) for i, r in enumerate(new_unique)]
    clean_50 = list(pilot_rows) + new_clean_30
    assert len(clean_50) == 50
    write_jsonl(OFFICIAL_CLEAN, clean_50)
    print(f"[5] clean 50写完成: {OFFICIAL_CLEAN.name}")

    # 6. 重新读取验证 count=50/50 （§八要求：不能只看写入成功，必须实际重新读取）
    raw_reload, _ = read_jsonl(OFFICIAL_RAW)
    clean_reload, _ = read_jsonl(OFFICIAL_CLEAN)
    print(f"\n[6] 实际重新读取验证:")
    print(f"    raw实际行数 = {len(raw_reload)}（期望50）")
    print(f"    clean实际行数 = {len(clean_reload)}（期望50）")
    if len(raw_reload) != 50 or len(clean_reload) != 50:
        print("REVIEW_REQUIRED: 正式50条行数校验失败 ❌")
        sys.exit(1)
    print("    ✅ PASS raw/clean 均=50")

    # 7. 额外：分布初检 T25/B25
    def count_tb(rows):
        t = sum(1 for r in rows if is_tencent(r.get("source_company") or ""))
        b = sum(1 for r in rows if is_bytedance(r.get("source_company") or ""))
        return t, b
    rt, rb = count_tb(raw_reload); ct, cb = count_tb(clean_reload)
    print(f"[7] RAW分布 T={rt} B={rb}（期望25/25） | CLEAN分布 T={ct} B={cb}（期望25/25）")
    if rt != 25 or rb != 25 or ct != 25 or cb != 25:
        print("REVIEW_REQUIRED: T/B分布未达到25/25 ❌")
        sys.exit(1)
    print("    ✅ PASS T=25 B=25")
    print("\n🎉 S4 正式50条 raw/clean 生成并验证通过！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
