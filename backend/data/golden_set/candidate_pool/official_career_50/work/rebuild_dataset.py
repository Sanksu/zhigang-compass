#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# rebuild_dataset.py — §一 强制放 work/，仅使用 Python open() 逐行读/写（无50KB截断）
# 位置：backend/data/golden_set/candidate_pool/official_career_50/work/rebuild_dataset.py
# 功能：
#  §二：程序化只读盘点Pilot20 + 三checkpoint → 8项统计 stdout
#  §三：生成 recovered_current_raw/clean.jsonl（Pilot20 20原文 + 新增唯一有效N条）
#  §四：重新读取 recovered_current 前20条 vs 原Pilot20六项 20/20保护校验 → 任何一项不字节级一致 exit(1) → REVIEW_REQUIRED
#  §五：输出缺口 T_remaining / B_remaining

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # official_career_50/
WORK = ROOT / "work"
PILOT20 = ROOT.parent / "official_career_pilot20" / "official_career_pilot20_clean.jsonl"

CK_FILES = [
    WORK / "batch_a_checkpoint.jsonl",
    WORK / "batch_b_checkpoint.jsonl",
    WORK / "batch_c_checkpoint.jsonl",
]

BATCH_D_NEW = WORK / "batch_d_checkpoint.jsonl"  # 后续S3补采字节13条（本轮S0-S2不写）
REC_RAW = WORK / "recovered_current_raw.jsonl"
REC_CLEAN = WORK / "recovered_current_clean.jsonl"

PILOT_SIX_FIELDS = ("source_id", "source_url", "responsibilities", "requirements", "detail_raw_text", "_sha256")


def read_jsonl(path: Path):
    """§一 逐行open()读，不一次性load大文本
    兼容两类checkpoint分隔：
      1) 标准JSONL：每行1个对象（真实换行\\x0A分隔）
      2) 伪JSONL：同1物理行上多个对象用字面量'\\n'(反斜杠+n)拼接，即'}\\n{'边界
    两类都需 strict=False 容忍JD正文内未转义的控制字符"""
    rows = []
    if not path.exists():
        return rows, []
    errs = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            s = line.strip()
            if not s:
                continue
            # Case 2: 同1物理行用 "}\\n{" 作为对象边界（字面量反斜杠+n）
            # 先按 "}\\n{" 拆分，再把首尾补回括号
            if "}\\n{" in s:
                chunks = s.split("}\\n{")
                segments = []
                for i, ch in enumerate(chunks):
                    if i == 0:
                        segments.append(ch + "}")
                    elif i == len(chunks) - 1:
                        segments.append("{" + ch)
                    else:
                        segments.append("{" + ch + "}")
            else:
                segments = [s]
            for seg_i, seg in enumerate(segments):
                seg = seg.strip()
                if not seg:
                    continue
                # 剥掉seg首尾残留的字面量 '\n'（反斜杠+n字符，不是真实换行）
                while seg.startswith("\\n"):
                    seg = seg[2:].strip()
                while seg.endswith("\\n"):
                    seg = seg[:-2].strip()
                if not seg:
                    continue
                try:
                    rows.append(json.loads(seg, strict=False))
                except Exception as e:  # noqa: BLE001
                    errs.append((lineno, f"seg{seg_i}: {e}", seg[:120]))
    return rows, errs


def write_jsonl(path: Path, rows):
    """§一 逐行open()写，确保不经过任何聊天截断"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False))
            fh.write("\n")
    os.replace(tmp, path)
    return True


def dedup_new_checkpoint(new_rows):
    """§二：按 source_id 去重 + 与Pilot20 sid/url 重复剔除"""
    pilot20_rows, _ = read_jsonl(PILOT20)
    p_sid = {r["source_id"] for r in pilot20_rows}
    p_url = {r["source_url"] for r in pilot20_rows}
    seen_sid = set()
    seen_url = set()
    out, dup_sid, dup_url, dup_p_sid, dup_p_url = [], [], [], [], []
    for r in new_rows:
        sid = r.get("source_id")
        url = r.get("source_url")
        if not sid or not url:
            continue
        if sid in seen_sid:
            dup_sid.append(sid); continue
        if url in seen_url:
            dup_url.append(url); continue
        if sid in p_sid:
            dup_p_sid.append(sid); continue
        if url in p_url:
            dup_p_url.append(url); continue
        seen_sid.add(sid); seen_url.add(url); out.append(r)
    return out, {
        "ck_total_raw": len(new_rows),
        "ck_sid_unique": len(seen_sid),
        "ck_url_unique": len(seen_url),
        "ck_internal_dup_sid": len(dup_sid),
        "ck_internal_dup_url": len(dup_url),
        "ck_dup_sid_pilot": len(dup_p_sid),
        "ck_dup_url_pilot": len(dup_p_url),
        "dup_sid_pilot_list": dup_p_sid,
        "dup_url_pilot_list": dup_p_url,
    }


def tb_split(new_rows):
    t = sum(1 for r in new_rows if ("Tencent" in r.get("source_company", "") or "腾讯" in r.get("source_company", "")))
    b = len(new_rows) - t
    return t, b


def cleanify_new(r, idx_new):
    """新增条目的clean结构：严格对齐Pilot20 24字段；禁止AI改写任何正文；resp/req/detail_raw按checkpoint原文字节级抄"""
    sc = r.get("source_company", "")
    is_tencent = ("Tencent" in sc or "腾讯" in sc)
    order = r.get("order", f"NEW{idx_new+1:02d}")
    # 简单岗位族分类（仅填充_direction/_rid元字段，绝不改动正文）：
    title = (r.get("job_title_raw") or "") + " " + (r.get("_category") or "")
    d = "算法" if any(k in title for k in ["算法", "Algorithm", "研究员", "语音", "NLP", "风控", "多模态", "大模型", "混元"]) else (
        "后端" if any(k in title for k in ["后台", "后端", "服务端", "架构师", "SRE", "全栈", "BFF", "PUBG", "UGC", "协作工具"]) else (
        "客户端" if any(k in title for k in ["客户端", "SDK", "3A级", "关卡", "战斗", "任务", "C++", "豆包"]) else (
        "前端" if any(k in title for k in ["前端", "小程序", "React"]) else (
        "测试" if "测试" in title else (
        "大数据" if any(k in title for k in ["大数据", "数据工程", "数仓", "Flink", "Spark", "Hive", "数据质量"]) else (
        "运维" if any(k in title for k in ["运维", "SRE", "可用性", "边缘"]) else (
        "安全" if any(k in title for k in ["安全", "逆向", "加密"]) else (
        "存储" if "存储" in title else (
        "图形渲染" if any(k in title for k in ["渲染", "图形", "Shader"]) else (
        "音视频SDK" if any(k in title for k in ["音视频", "SDK", "秒剪"]) else (
        "Infra/基础架构" if any(k in title for k in ["Infra", "基础架构", "全球流量", "Kubernetes", "基础设施"]) else "其他")))))))))))
    return {
        "source": r.get("source", "official_career_site"),
        "source_company": "腾讯" if is_tencent else sc,
        "source_id": r["source_id"],
        "source_id_method": "postId" if is_tencent else "URL_path_19_digit_job_id",
        "source_url": r["source_url"],
        "job_title_raw": r["job_title_raw"],
        "company_name": r.get("company_name", ("腾讯" if is_tencent else "北京字节跳动网络技术有限公司")),
        "location": r.get("location", ""),
        "salary": r.get("salary", None),
        "source_education": r.get("source_education", None),
        "source_experience": r.get("source_experience", None),
        "publish_time": r.get("publish_time", None),
        "responsibilities": r["responsibilities"],
        "requirements": r["requirements"],
        "detail_raw_text": r["detail_raw_text"],
        "crawl_time": r.get("crawl_time"),
        "_sha256": r.get("_sha256", ("00000000000000000000000000000000000000000000000000000000000000" + str(max(1, idx_new+1)))[-64:]),  # 临时占位；后续S5统一重算>>>0版
        "_direction": d,
        "_rid": f"NEW-{order}",
        "_simhash64": r.get("_simhash64", None),
        "_visit_status": "正常访问",
        "_display_position_id": None,
        "_job_category_raw": r.get("_category", None),
        "_adapter_parse_success": True,
    }


def verify_pilot20_six_protection(recovered_rows, pilot_rows):
    """§四：重新读取 recovered 前20条 × 原Pilot20六项 → 必须字节级20/20一致。任何不一致立即停止并exit(1)"""
    if len(pilot_rows) != 20:
        print("REVIEW_REQUIRED: Pilot20原文件不是20条（实际=", len(pilot_rows), "）", flush=True); return False
    if len(recovered_rows) < 20:
        print("REVIEW_REQUIRED: recovered_current <20行", flush=True); return False
    failed = []
    for i in range(20):
        p = pilot_rows[i]; rec = recovered_rows[i]
        for f in PILOT_SIX_FIELDS:
            pv = p.get(f, None); rv = rec.get(f, None)
            if pv != rv:
                failed.append((i+1, f, type(pv).__name__, type(rv).__name__,
                               (str(pv)[:80] + "…") if len(str(pv))>80 else str(pv),
                               (str(rv)[:80] + "…") if len(str(rv))>80 else str(rv)))
    if failed:
        print("REVIEW_REQUIRED: Pilot20六项保护失败（共", len(failed), "处不一致）", flush=True)
        for (ln, f, tp, tr, pv, rv) in failed[:10]:
            print(f"  L{ln} field={f} pilot_type={tp} rec_type={tr}", flush=True)
            print(f"    pilot= {pv}", flush=True)
            print(f"    recd = {rv}", flush=True)
        return False
    print("PASS: Pilot20六项保护 20/20 字节级完全一致 ✅", flush=True)
    return True


def main():
    # ===== §二 只读盘点（不写任何文件）=====
    pilot_rows, _ = read_jsonl(PILOT20)
    ck_all = []
    for p in CK_FILES:
        rows, errs = read_jsonl(p)
        if errs:
            print(f"WARN checkpoint {p.name} parse错误 {len(errs)}处（跳过坏行）", flush=True)
        ck_all.extend(rows)
    new_unique, stats = dedup_new_checkpoint(ck_all)
    t_new, b_new = tb_split(new_unique)
    stats["Tencent_current_new"] = t_new
    stats["ByteDance_current_new"] = b_new
    stats["Tencent_remaining"] = 15 - t_new
    stats["ByteDance_remaining"] = 15 - b_new
    stats["NEW_VALID_COUNT"] = len(new_unique)
    stats["REMAINING_total"] = 30 - len(new_unique)
    stats["Pilot20_original"] = len(pilot_rows)

    print("==== §二 只读盘点（程序化文件结果）====", flush=True)
    for k, v in stats.items():
        print(f"  {k}: {v}", flush=True)

    # ===== §三 生成中间集 recovered_current（仅写work/，不动正式raw/clean）=====
    rec_raw = list(pilot_rows) + list(new_unique)  # Pilot20原文 前20条：列表引用浅拷贝，不对正文做任何改动
    write_jsonl(REC_RAW, rec_raw)

    new_clean_rows = [cleanify_new(r, i) for i, r in enumerate(new_unique)]
    # clean的前20条必须是pilot_clean原文（与原pilot_clean字节级一致，因为pilot_rows本身就是从pilot_clean读出来的clean结构）
    rec_clean = list(pilot_rows) + new_clean_rows
    write_jsonl(REC_CLEAN, rec_clean)

    # ===== §四 重新读回 recovered_current 核验 Pilot20 保护 =====
    rec_raw_reload, _ = read_jsonl(REC_RAW)
    ok = verify_pilot20_six_protection(rec_raw_reload, pilot_rows)
    if not ok:
        sys.exit(1)

    rec_clean_reload, _ = read_jsonl(REC_CLEAN)
    ok2 = verify_pilot20_six_protection(rec_clean_reload, pilot_rows)
    if not ok2:
        sys.exit(1)

    print("\n==== §三 恢复中间集 ====", flush=True)
    print(f"  recovered_current_raw实际行数 (重新读取) = {len(rec_raw_reload)}", flush=True)
    print(f"  recovered_current_clean实际行数 (重新读取) = {len(rec_clean_reload)}", flush=True)
    print(f"  (应为 Pilot20 {len(pilot_rows)} + 新增唯一 {len(new_unique)} = {len(pilot_rows)+len(new_unique)})", flush=True)

    print("\n==== §五 缺口计算 ====", flush=True)
    print(f"  新增目标 = 30；当前新增唯一 = {len(new_unique)}；还缺 = {stats['REMAINING_total']}", flush=True)
    print(f"  Tencent 新增目标 = 15；当前 = {t_new}；还缺 = {stats['Tencent_remaining']}", flush=True)
    print(f"  ByteDance 新增目标 = 15；当前 = {b_new}；还缺 = {stats['ByteDance_remaining']}", flush=True)

    # 给后续脚本传缺口（也写到work便于读取）
    with open(WORK / "inventory_gap.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print("\nOK inventory_gap.json 已写 work/（§五缺口存档）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
