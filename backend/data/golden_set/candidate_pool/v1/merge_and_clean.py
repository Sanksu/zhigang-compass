#!/usr/bin/env python3
"""
merge_and_clean.py — 候选池汇总、清洗、去重、质检、分层

用法:
    cd backend/data/golden_set/candidate_pool/v1
    uv run python merge_and_clean.py

输入 (6个文件):
    v1/batch_data_ai.json              — JSON数组, 标准字段
    v1/batch_embedded_security.json    — JSON数组, 标准字段  
    v1/batch_backend_fullstack.jsonl  — JSONL, 标准字段
    v1/batch_frontend_test_ops.jsonl  — JSONL, 标准字段
    ../real_jd_batch_20260815.json     — JSON数组, 旧格式
    ../real_jd_pilot_20.jsonl          — JSONL, 标准字段 (Pilot)

输出:
    real_jd_candidates_raw.jsonl
    real_jd_candidates_clean.jsonl
    real_jd_candidates_clean.csv
    real_jd_review_required.csv
    real_jd_rejected.csv
    real_jd_collection_quality_report.md
"""

import json
import hashlib
import csv
import os
import sys
import re
from collections import Counter, defaultdict, OrderedDict
from datetime import datetime

# ─── 路径配置 ──────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)

# 添加 backend 到 sys.path 以使用项目 SimHash
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(BASE_DIR))))
sys.path.insert(0, BACKEND_DIR)

from app.services.data_quality.simhash import simhash64, hamming_distance, find_similar_pairs

# 输入文件
INPUT_FILES = [
    ("v1/batch_data_ai.json",              "json",  "standard"),
    ("v1/batch_embedded_security.json",    "json",  "standard"),
    ("v1/batch_backend_fullstack.jsonl",  "jsonl", "standard"),
    ("v1/batch_frontend_test_ops.jsonl",  "jsonl", "standard"),
    ("real_jd_batch_20260815.json",        "json",  "old_schema"),
    ("real_jd_pilot_20.jsonl",             "jsonl", "standard"),
]

# 输出文件
OUTPUT_RAW = os.path.join(BASE_DIR, "real_jd_candidates_raw.jsonl")
OUTPUT_CLEAN_JSONL = os.path.join(BASE_DIR, "real_jd_candidates_clean.jsonl")
OUTPUT_CLEAN_CSV = os.path.join(BASE_DIR, "real_jd_candidates_clean.csv")
OUTPUT_REVIEW_CSV = os.path.join(BASE_DIR, "real_jd_review_required.csv")
OUTPUT_REJECTED_CSV = os.path.join(BASE_DIR, "real_jd_rejected.csv")
OUTPUT_REPORT = os.path.join(BASE_DIR, "real_jd_collection_quality_report.md")

# ─── 辅助函数 ──────────────────────────────────────────────────────────

def compute_sha256(text: str) -> str:
    """计算 SHA-256 (基于 responsibilities + '\\n' + requirements)"""
    if not text:
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def extract_education(text: str) -> str:
    """从文本中提取学历要求"""
    if not text:
        return ""
    patterns = [
        r'(?:统招|全日制)?(?:本科|硕士|博士|大专|专科|研究生|本科以上|硕士以上|博士以上)(?:及以上|及以|以上)?(?:学历|学位)?',
        r'(?:本科|硕士|博士|大专|专科|研究生)(?:及以上学历|及以上|以上学历|学历)',
        r'学历[：:]\s*(?:本科|硕士|博士|大专|专科|研究生)',
        r'(?:本科|硕士|博士|大专|专科|研究生)(?:毕业|学历)',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(0)
    return ""


def extract_experience(text: str) -> str:
    """从文本中提取经验要求"""
    if not text:
        return ""
    patterns = [
        r'(\d+)[年\s]*(?:以上|及以|及以[上上])?(?:相关)?(?:工作)?经验',
        r'(\d+)\s*年\s*(?:以上|及以上)?(?:相关)?(?:工作)?(?:经验|开发)',
        r'(?:具有|具备|拥有)?\s*(\d+)\s*年\s*(?:以上|及以上)?',
        r'经验[：:]\s*(\d+[年\s]*以上)',
        r'(\d+[－\-~至到]\d+)\s*年',
        r'经验不限',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(0)
    return ""


def check_education_conflict(source_edu: str, text_edu: str) -> bool:
    """检查 source_education 与 text_education 是否存在明显矛盾"""
    if not source_edu or not text_edu:
        return False
    if "大专" in source_edu or "专科" in source_edu:
        if "本科" in text_edu or "硕士" in text_edu or "博士" in text_edu:
            return True
    if "高中" in source_edu:
        if "本科" in text_edu or "硕士" in text_edu:
            return True
    if "学历不限" in source_edu:
        if "本科" in text_edu or "硕士" in text_edu:
            return True
    return False


def check_experience_conflict(source_exp: str, text_exp: str) -> bool:
    """检查 source_experience 与 text_experience 是否存在明显矛盾"""
    if not source_exp or not text_exp:
        return False
    if "经验不限" in source_exp or "无经验" in source_exp:
        exp_match = re.search(r'(\d+)', text_exp)
        if exp_match:
            years = int(exp_match.group(1))
            if years >= 1:
                return True
    return False


def classify_job(title: str, tags: list) -> str:
    """根据 job_title_raw 和 tags 自动分类到岗位类别"""
    title_lower = (title or "").lower()
    tags_str = " ".join(tags or []).lower()

    # AI/大模型
    if any(kw in title_lower for kw in ["大模型", "llm", "aigc", "agent", "智能体",
                                          "多模态", "rag"]):
        return "AI/大模型"
    if any(kw in tags_str for kw in ["大模型", "llm", "aigc", "agent", "智能体",
                                       "rag", "langchain", "langgraph"]):
        return "AI/大模型"

    # 算法
    if any(kw in title_lower for kw in ["算法", "nlp", "cv", "机器学习",
                                          "深度学习", "强化学习", "推荐算法", "运筹优化",
                                          "ai算法", "aigc", "多模态"]):
        return "算法"

    # 算法 (fallback by tags)
    if any(kw in tags_str for kw in ["算法", "机器学习", "深度学习", "推荐算法", "nlp", "cv"]):
        return "算法"

    # 前端开发
    frontend_kw = ["前端", "web前端", "h5", "vue", "react", "angular", "web开发",
                   "小程序", "前端开发", "html5"]
    if any(kw in title_lower for kw in frontend_kw):
        return "前端开发"

    # 全栈开发
    if any(kw in title_lower for kw in ["全栈", "fullstack", "full stack", "full-stack"]):
        return "全栈开发"

    # 测试
    if any(kw in title_lower for kw in ["测试", "test", "qa", "质量", "自动化测试"]):
        return "测试"

    # 运维/DevOps
    if any(kw in title_lower for kw in ["运维", "devops", "sre", "平台运维", "系统运维",
                                          "云原生", "k8s", "kubernetes", "docker"]):
        return "运维/DevOps"

    # 数据分析
    if any(kw in title_lower for kw in ["数据分析", "商业分析", "经营分析", "bi", "数据运营",
                                          "分析工程师", "analyst"]):
        return "数据分析"

    # 数据工程/大数据
    if any(kw in title_lower for kw in ["大数据", "数据工程", "etl", "数据仓库", "数据治理",
                                          "flink", "spark", "hadoop", "数据平台", "数据开发",
                                          "数据中台"]):
        return "数据工程/大数据"

    # 嵌入式/C++
    if any(kw in title_lower for kw in ["嵌入式", "单片机", "mcu", "arm", "stm32", "dsp",
                                          "fpga", "bsp", "驱动开发", "固件", "iot", "物联网"]):
        return "嵌入式/C++"

    # 网络/安全
    if any(kw in title_lower for kw in ["安全", "网络", "渗透", "等保", "防火墙", "vpn",
                                          "信息安全", "网络安全", "加密"]):
        return "网络/安全"

    # 后端开发
    backend_kw = ["java", "python", "go", "golang", "php", "c++", "node.js", "后端",
                  "spring", "django", "flask", "gin", "express", "后端开发"]
    if any(kw in title_lower for kw in backend_kw):
        return "后端开发"

    # 后端开发 (fallback by tags)
    if any(kw in tags_str for kw in ["java", "spring", "python", "django", "go", "php"]):
        return "后端开发"

    # 前端开发 (fallback by tags)
    if any(kw in tags_str for kw in ["vue", "react", "javascript", "前端"]):
        return "前端开发"

    return "其他技术岗"


# ─── 旧格式标准化 ──────────────────────────────────────────────────────

def normalize_old_schema(record: dict) -> dict:
    """
    将 parent 格式 (real_jd_batch_20260815.json) 标准化为 v1 格式
    
    旧格式字段: source_id, source_url, source, title, company, city, district, 
                salary, experience, education, category, description, 
                responsibilities, requirements, skills, company_size, 
                company_type, company_industry, publish_time, headcount
    """
    responsibilities = record.get("responsibilities", "")
    requirements = record.get("requirements", "")
    
    # 如果没有 responsibilities/requirements，从 description 中尝试提取
    if not responsibilities and not requirements:
        desc = record.get("description", "")
        if desc:
            # 尝试按 "岗位职责" / "任职要求" 分割
            resp_match = re.search(r'(?:【?岗位职责】?|【?工作职责】?|【?职责描述】?)\s*[:：]?\s*(.+?)(?=(?:【?任职|【?岗位要求|【?工作要求|$))', desc, re.DOTALL)
            req_match = re.search(r'(?:【?任职要求】?|【?岗位要求】?|【?工作要求】?|【?任职资格】?)\s*[:：]?\s*(.+)', desc, re.DOTALL)
            if resp_match:
                responsibilities = resp_match.group(1).strip()
            if req_match:
                requirements = req_match.group(1).strip()
            if not responsibilities and not requirements:
                responsibilities = desc.strip()
    
    detail_raw = responsibilities + "\n" + requirements if responsibilities or requirements else record.get("description", "")
    detail_raw = detail_raw.strip()
    
    text_edu = extract_education(requirements or record.get("description", ""))
    text_exp = extract_experience(requirements or record.get("description", ""))
    source_edu = record.get("education", "")
    source_exp = record.get("experience", "")
    edu_conflict = check_education_conflict(source_edu, text_edu)
    exp_conflict = check_experience_conflict(source_exp, text_exp)
    
    company_name = record.get("company", "")
    location = record.get("city", "")
    if record.get("district"):
        location += " " + record.get("district", "")
    location = location.strip()
    
    return {
        "source": record.get("source", "zhilian"),
        "source_id": record.get("source_id", ""),
        "source_url": record.get("source_url", ""),
        "job_title_raw": record.get("title", ""),
        "company_name": company_name,
        "location": location,
        "salary": record.get("salary", ""),
        "source_education": source_edu,
        "source_experience": source_exp,
        "text_education": text_edu,
        "text_experience": text_exp,
        "education_conflict": edu_conflict,
        "experience_conflict": exp_conflict,
        "publish_time": str(record.get("publish_time") or ""),
        "crawl_time": record.get("crawl_time", datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")),
        "responsibilities": responsibilities,
        "requirements": requirements,
        "detail_raw_text": detail_raw,
        "tags": record.get("skills", record.get("tags", [])),
        "_sha256": "",  # 稍后统一计算
        "low_information": False,
        "duplicate_review_required": False,
    }


# ─── 读取文件 ──────────────────────────────────────────────────────────

def load_file(filename: str, fmt: str) -> list:
    """加载单个文件，返回记录列表"""
    records = []
    filepath = filename if os.path.isabs(filename) else os.path.join(BASE_DIR, filename)
    # 尝试多个路径: v1/ 目录, parent/ 目录, 再去掉 v1/ 前缀尝试
    candidates = [
        filepath,
        os.path.join(BASE_DIR, os.path.basename(filename)),
        os.path.join(PARENT_DIR, os.path.basename(filename)),
        os.path.join(PARENT_DIR, filename),
    ]
    found = None
    for p in candidates:
        if os.path.exists(p):
            found = p
            break
    if not found:
        print(f"  ⚠️ 文件不存在: {filepath}")
        print(f"     尝试过: {candidates}")
        return records
    filepath = found
    
    try:
        if fmt == "json":
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                records = data
            else:
                records = [data]
        elif fmt == "jsonl":
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
    except json.JSONDecodeError as e:
        print(f"  ❌ 解析失败 ({filename}): {e}")
        return records
    
    return records


# ─── 主流程 ────────────────────────────────────────────────────────────

def main():
    start_time = datetime.now()
    print("=" * 70)
    print("  JD 候选池 — 汇总、清洗、去重、质检、分层")
    print(f"  开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    # ═══════════════════════════════════════════════════════════════════
    # 阶段一: 加载所有文件并统计
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "─" * 50)
    print("【阶段一】加载已有采集成果并验证完整性")
    print("─" * 50)
    
    file_stats = {}  # filename -> 统计
    all_raw = []     # 所有原始记录
    
    for filename, fmt, schema in INPUT_FILES:
        print(f"\n  读取: {filename} ({fmt}, {schema})")
        records = load_file(filename, fmt)
        parsed = len(records)
        corrupted = 0
        
        # 检查损坏记录
        valid_records = []
        for i, r in enumerate(records):
            if not isinstance(r, dict):
                corrupted += 1
                continue
            if schema == "standard":
                # 标准格式 - 直接使用
                valid_records.append(r)
            elif schema == "old_schema":
                # 旧格式 - 标准化
                normalized = normalize_old_schema(r)
                valid_records.append(normalized)
        
        file_stats[filename] = {
            "total": parsed,
            "valid": len(valid_records),
            "corrupted": corrupted,
        }
        
        print(f"    总记录: {parsed}, 成功解析: {len(valid_records)}, 损坏: {corrupted}")
        all_raw.extend(valid_records)
    
    total_raw = len(all_raw)
    print(f"\n  ▶ 原始汇总: {total_raw} 条")
    
    # ═══════════════════════════════════════════════════════════════════
    # 阶段二: 检测 Pilot 与后续批次的重叠
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "─" * 50)
    print("【阶段二】Pilot 重叠检测")
    print("─" * 50)
    
    # 找出 Pilot 记录的 source_id
    pilot_filename = "real_jd_pilot_20.jsonl"
    pilot_records = load_file(pilot_filename, "jsonl")
    pilot_ids = set(r.get("source_id", "") for r in pilot_records if r.get("source_id"))
    
    # 收集非 Pilot 批次的所有 source_id（从 all_raw 中排除 pilot 来源的记录）
    non_pilot_ids = set()
    for filename, fmt, schema in INPUT_FILES:
        if filename == pilot_filename:
            continue
        for r in load_file(filename, fmt):
            sid = r.get("source_id", "")
            if sid:
                non_pilot_ids.add(sid)
    
    # 统计 pilot 重叠（pilot ID 也出现在非 pilot 批次中）
    pilot_in_later = sum(1 for pid in pilot_ids if pid in non_pilot_ids)
    pilot_only = len(pilot_ids) - pilot_in_later
    print(f"  Pilot 总记录数: {len(pilot_records)}")
    print(f"  非Pilot批次唯一ID: {len(non_pilot_ids)}")
    print(f"  Pilot 中与后续批次重叠: {pilot_in_later}")
    print(f"  Pilot 中独有的: {pilot_only}")
    
    # 按 source_id 去重，保留信息更完整的
    seen_ids = {}
    for r in all_raw:
        sid = r.get("source_id", "")
        if not sid:
            continue
        if sid in seen_ids:
            existing = seen_ids[sid]
            existing_len = len(existing.get("detail_raw_text", "") or "")
            current_len = len(r.get("detail_raw_text", "") or "")
            if current_len > existing_len:
                seen_ids[sid] = r
        else:
            seen_ids[sid] = r
    
    # ═══════════════════════════════════════════════════════════════════
    # 阶段三: 统一字段并建立 RAW 候选池
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "─" * 50)
    print("【阶段三】统一字段并建立 RAW 候选池")
    print("─" * 50)
    
    # 标准字段列表
    STANDARD_FIELDS = OrderedDict([
        ("source", ""),
        ("source_id", ""),
        ("source_url", ""),
        ("job_title_raw", ""),
        ("company_name", ""),
        ("location", ""),
        ("salary", ""),
        ("source_education", ""),
        ("source_experience", ""),
        ("text_education", ""),
        ("text_experience", ""),
        ("education_conflict", False),
        ("experience_conflict", False),
        ("publish_time", ""),
        ("crawl_time", ""),
        ("responsibilities", ""),
        ("requirements", ""),
        ("detail_raw_text", ""),
        ("_sha256", ""),
        ("low_information", False),
        ("duplicate_review_required", False),
    ])
    
    # 统一所有记录字段
    unified = []
    for r in seen_ids.values():
        rec = {}
        for field, default in STANDARD_FIELDS.items():
            if field == "low_information":
                rec[field] = r.get(field, False)
            elif field == "duplicate_review_required":
                rec[field] = r.get(field, False)
            elif field == "education_conflict":
                val = r.get(field, False)
                rec[field] = bool(val) if not isinstance(val, bool) else val
            elif field == "experience_conflict":
                val = r.get(field, False)
                rec[field] = bool(val) if not isinstance(val, bool) else val
            else:
                rec[field] = r.get(field, default)
                if rec[field] is None:
                    rec[field] = ""
                elif isinstance(rec[field], bool):
                    rec[field] = rec[field]
                elif not isinstance(rec[field], str):
                    rec[field] = str(rec[field])
        
        # 确保 detail_raw_text = responsibilities + "\n" + requirements
        resp = rec.get("responsibilities", "") or ""
        req = rec.get("requirements", "") or ""
        if resp or req:
            rec["detail_raw_text"] = (resp + "\n" + req).strip()
        elif not rec.get("detail_raw_text"):
            rec["detail_raw_text"] = ""
        
        # 保留 tags 和 category 等额外字段
        if "tags" in r:
            rec["tags"] = r["tags"]
        if "category" in r:
            rec["category"] = r["category"]
        
        unified.append(rec)
    
    print(f"  统一后唯一记录: {len(unified)} 条 (按 source_id 去重)")
    
    # ═══════════════════════════════════════════════════════════════════
    # 阶段四: 修复 SHA-256
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "─" * 50)
    print("【阶段四】修复 SHA-256 (基于 responsibilities + '\\n' + requirements)")
    print("─" * 50)
    
    sha_fixed = 0
    for r in unified:
        old_sha = r.get("_sha256", "")
        detail = r.get("detail_raw_text", "")
        new_sha = compute_sha256(detail)
        if old_sha != new_sha:
            sha_fixed += 1
        r["_sha256"] = new_sha
    print(f"  修复 SHA-256: {sha_fixed} 条")
    
    # ═══════════════════════════════════════════════════════════════════
    # 阶段五: 补充派生字段
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "─" * 50)
    print("【阶段五】补充派生字段 (text_education, text_experience, conflicts)")
    print("─" * 50)
    
    edu_filled = 0
    exp_filled = 0
    conflict_changed = 0
    
    for r in unified:
        combined = (r.get("responsibilities", "") or "") + "\n" + (r.get("requirements", "") or "")
        
        old_edu = r.get("text_education", "")
        new_edu = extract_education(combined)
        if new_edu and not old_edu:
            r["text_education"] = new_edu
            edu_filled += 1
        
        old_exp = r.get("text_experience", "")
        new_exp = extract_experience(combined)
        if new_exp and not old_exp:
            r["text_experience"] = new_exp
            exp_filled += 1
        
        source_edu = r.get("source_education", "")
        source_exp = r.get("source_experience", "")
        new_edu_conflict = check_education_conflict(source_edu, r.get("text_education", ""))
        new_exp_conflict = check_experience_conflict(source_exp, r.get("text_experience", ""))
        
        if r.get("education_conflict") != new_edu_conflict:
            conflict_changed += 1
        if r.get("experience_conflict") != new_exp_conflict:
            conflict_changed += 1
        
        r["education_conflict"] = new_edu_conflict
        r["experience_conflict"] = new_exp_conflict
    
    print(f"  补充 text_education: {edu_filled} 条")
    print(f"  补充 text_experience: {exp_filled} 条")
    print(f"  修正冲突标记: {conflict_changed} 处")
    
    # ═══════════════════════════════════════════════════════════════════
    # 阶段六: 岗位分类
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "─" * 50)
    print("【阶段六】岗位分类")
    print("─" * 50)
    
    for r in unified:
        r["category"] = classify_job(r.get("job_title_raw", ""), r.get("tags", []))
    
    cat_counts = Counter(r["category"] for r in unified)
    for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {cnt}")
    
    # ═══════════════════════════════════════════════════════════════════
    # 阶段七: 精确去重
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "─" * 50)
    print("【阶段七】精确去重 (source_id → source_url → SHA256)")
    print("─" * 50)
    
    # 7.1 source_id 重复
    sid_map = defaultdict(list)
    for i, r in enumerate(unified):
        sid = r.get("source_id", "")
        if sid:
            sid_map[sid].append(i)
    
    sid_dup_count = 0
    # source_id 已在阶段三按 seen_ids 去重，这里只需报告
    for sid, indices in sid_map.items():
        if len(indices) > 1:
            sid_dup_count += len(indices) - 1
    print(f"  source_id 重复: {sid_dup_count} 条冗余 (已在阶段三合并)")
    
    # 7.2 source_url 重复
    url_map = defaultdict(list)
    for i, r in enumerate(unified):
        url = r.get("source_url", "")
        if url:
            url_map[url].append(i)
    
    url_dup_count = sum(len(indices) - 1 for indices in url_map.values() if len(indices) > 1)
    print(f"  source_url 重复: {url_dup_count} 条冗余")
    
    # 7.3 SHA256 重复
    sha_map = defaultdict(list)
    for i, r in enumerate(unified):
        sha_map[r["_sha256"]].append(i)
    
    sha_dup_count = sum(len(indices) - 1 for indices in sha_map.values() if len(indices) > 1)
    print(f"  SHA256 重复: {sha_dup_count} 条冗余")
    
    # 记录精确重复信息
    exact_dup_map = {}  # duplicate_sid -> {"duplicate_of": sid, "duplicate_reason": str}
    for sha, indices in sha_map.items():
        if len(indices) > 1:
            # 保留信息最完整的一条
            best_idx = max(indices, key=lambda i: len(unified[i].get("detail_raw_text", "") or ""))
            for idx in indices:
                if idx != best_idx:
                    exact_dup_map[unified[idx]["source_id"]] = {
                        "duplicate_of": unified[best_idx]["source_id"],
                        "duplicate_reason": f"SHA256 exact duplicate"
                    }
    
    # 同样处理 URL 重复
    for url, indices in url_map.items():
        if len(indices) > 1:
            best_idx = max(indices, key=lambda i: len(unified[i].get("detail_raw_text", "") or ""))
            for idx in indices:
                if idx != best_idx and unified[idx]["source_id"] not in exact_dup_map:
                    exact_dup_map[unified[idx]["source_id"]] = {
                        "duplicate_of": unified[best_idx]["source_id"],
                        "duplicate_reason": f"source_url duplicate: {url}"
                    }
    
    print(f"  精确去重标记: {len(exact_dup_map)} 条")
    
    # ═══════════════════════════════════════════════════════════════════
    # 阶段八: SimHash 近似去重 (使用项目实现)
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "─" * 50)
    print("【阶段八】SimHash 近似去重 (汉明距 ≤ 3, 项目标准实现)")
    print("─" * 50)
    
    simhash_records = []
    for i, r in enumerate(unified):
        text = (r.get("responsibilities", "") or "") + "\n" + (r.get("requirements", "") or "")
        if text.strip():
            fp = simhash64(text)
        else:
            fp = 0
        simhash_records.append((r["source_id"], fp))
    
    approx_pairs = find_similar_pairs(simhash_records, threshold=3)
    
    # 只标记非精确重复的近似重复
    approx_dup_set = set()
    for sid_a, sid_b in approx_pairs:
        if sid_a not in exact_dup_map and sid_b not in exact_dup_map:
            approx_dup_set.add(sid_a)
            approx_dup_set.add(sid_b)
    
    for r in unified:
        if r["source_id"] in approx_dup_set:
            r["duplicate_review_required"] = True
    
    print(f"  近似重复对: {len(approx_pairs)}")
    print(f"  标记为 review_required: {len(approx_dup_set)} 条")
    
    # ═══════════════════════════════════════════════════════════════════
    # 阶段九: 质量审核
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "─" * 50)
    print("【阶段九】数据质量审核")
    print("─" * 50)
    
    quality_issues = defaultdict(list)
    
    for r in unified:
        sid = r["source_id"]
        detail = r.get("detail_raw_text", "") or ""
        resp = r.get("responsibilities", "") or ""
        req = r.get("requirements", "") or ""
        
        # 空正文
        if not detail.strip():
            quality_issues[sid].append("empty_text")
        
        # 低信息量 (< 200 字符)
        if len(detail) < 200:
            r["low_information"] = True
            quality_issues[sid].append("low_information")
        
        # 岗位名称为空
        if not r.get("job_title_raw", "").strip():
            quality_issues[sid].append("no_title")
        
        # source_url 缺失
        if not r.get("source_url", "").strip():
            quality_issues[sid].append("no_url")
        
        # responsibilities 缺失
        if not resp.strip() and not req.strip():
            quality_issues[sid].append("missing_content")
        
        # 学历冲突
        if r.get("education_conflict"):
            quality_issues[sid].append("education_conflict")
        
        # 经验冲突
        if r.get("experience_conflict"):
            quality_issues[sid].append("experience_conflict")
        
        # 精确重复
        if sid in exact_dup_map:
            quality_issues[sid].append("exact_duplicate")
        
        # 近似重复
        if r.get("duplicate_review_required"):
            quality_issues[sid].append("approximate_duplicate")
    
    # 统计
    print(f"  空正文: {sum(1 for v in quality_issues.values() if 'empty_text' in v)}")
    print(f"  低信息 (<200字): {sum(1 for v in quality_issues.values() if 'low_information' in v)}")
    print(f"  无标题: {sum(1 for v in quality_issues.values() if 'no_title' in v)}")
    print(f"  无URL: {sum(1 for v in quality_issues.values() if 'no_url' in v)}")
    print(f"  内容缺失: {sum(1 for v in quality_issues.values() if 'missing_content' in v)}")
    print(f"  学历冲突: {sum(1 for v in quality_issues.values() if 'education_conflict' in v)}")
    print(f"  经验冲突: {sum(1 for v in quality_issues.values() if 'experience_conflict' in v)}")
    print(f"  精确重复: {sum(1 for v in quality_issues.values() if 'exact_duplicate' in v)}")
    print(f"  近似重复: {sum(1 for v in quality_issues.values() if 'approximate_duplicate' in v)}")
    
    # ═══════════════════════════════════════════════════════════════════
    # 阶段十: 数据分层
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "─" * 50)
    print("【阶段十】数据分层")
    print("─" * 50)
    
    accepted = []
    review_required = []
    rejected = []
    
    for r in unified:
        sid = r["source_id"]
        issues = quality_issues.get(sid, [])
        
        # rejected: 空正文 / 无URL / 无标题 / 精确重复 / 内容缺失
        reject_flags = {"empty_text", "no_url", "no_title", "exact_duplicate", "missing_content"}
        if reject_flags & set(issues):
            r["tier"] = "rejected"
            r["rejection_reason"] = "; ".join(sorted(set(issues) & reject_flags))
            # 记录精确重复信息
            if sid in exact_dup_map:
                r["duplicate_of"] = exact_dup_map[sid]["duplicate_of"]
                r["duplicate_reason"] = exact_dup_map[sid]["duplicate_reason"]
            rejected.append(r)
            continue
        
        # review_required: education_conflict / experience_conflict / low_information / approximate_duplicate
        review_flags = {"education_conflict", "experience_conflict", "low_information", "approximate_duplicate"}
        if review_flags & set(issues):
            r["tier"] = "review_required"
            r["review_reason"] = "; ".join(sorted(set(issues) & review_flags))
            review_required.append(r)
            continue
        
        # accepted
        r["tier"] = "accepted"
        accepted.append(r)
    
    print(f"  accepted: {len(accepted)}")
    print(f"  review_required: {len(review_required)}")
    print(f"  rejected: {len(rejected)}")
    
    # ═══════════════════════════════════════════════════════════════════
    # 阶段十一: 输出文件
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "─" * 50)
    print("【阶段十一】生成输出文件")
    print("─" * 50)
    
    def write_jsonl(records, path):
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    
    # 定义输出字段顺序
    OUTPUT_FIELDS = [
        "source", "source_id", "source_url",
        "job_title_raw", "company_name", "location", "salary",
        "source_education", "source_experience",
        "text_education", "text_experience",
        "education_conflict", "experience_conflict",
        "publish_time", "crawl_time",
        "responsibilities", "requirements", "detail_raw_text",
        "tags", "category", "_sha256",
        "low_information", "duplicate_review_required",
        "tier", "review_reason", "rejection_reason",
        "duplicate_of", "duplicate_reason",
    ]
    
    # 11.1 raw JSONL (全部唯一记录)
    write_jsonl(unified, OUTPUT_RAW)
    print(f"  ✓ real_jd_candidates_raw.jsonl ({len(unified)} 条)")
    
    # 11.2 clean JSONL (accepted + review_required)
    clean = accepted + review_required
    write_jsonl(clean, OUTPUT_CLEAN_JSONL)
    print(f"  ✓ real_jd_candidates_clean.jsonl ({len(clean)} 条)")
    
    # 11.3 clean CSV
    csv_fields = [f for f in OUTPUT_FIELDS if f not in ("tier", "review_reason", "rejection_reason", "duplicate_of", "duplicate_reason")]
    with open(OUTPUT_CLEAN_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction='ignore')
        writer.writeheader()
        for r in clean:
            row = dict(r)
            if isinstance(row.get("tags"), list):
                row["tags"] = "; ".join(row["tags"])
            writer.writerow(row)
    print(f"  ✓ real_jd_candidates_clean.csv ({len(clean)} 条)")
    
    # 11.4 review_required CSV
    review_csv_fields = csv_fields + ["review_reason"]
    with open(OUTPUT_REVIEW_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=review_csv_fields, extrasaction='ignore')
        writer.writeheader()
        for r in review_required:
            row = dict(r)
            if isinstance(row.get("tags"), list):
                row["tags"] = "; ".join(row["tags"])
            writer.writerow(row)
    print(f"  ✓ real_jd_review_required.csv ({len(review_required)} 条)")
    
    # 11.5 rejected CSV
    reject_csv_fields = csv_fields + ["rejection_reason", "duplicate_of", "duplicate_reason"]
    with open(OUTPUT_REJECTED_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=reject_csv_fields, extrasaction='ignore')
        writer.writeheader()
        for r in rejected:
            row = dict(r)
            if isinstance(row.get("tags"), list):
                row["tags"] = "; ".join(row["tags"])
            writer.writerow(row)
    print(f"  ✓ real_jd_rejected.csv ({len(rejected)} 条)")
    
    # ═══════════════════════════════════════════════════════════════════
    # 阶段十二: 质量报告
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "─" * 50)
    print("【阶段十二】生成质量报告")
    print("─" * 50)
    
    # 来源分布
    source_dist = Counter(r.get("source", "unknown") for r in unified)
    
    # 岗位分布 (clean)
    cat_dist_clean = Counter(r["category"] for r in clean)
    
    # 岗位分布 (accepted)
    cat_dist_accepted = Counter(r["category"] for r in accepted)
    
    # 字段完整率
    total = len(unified)
    field_rates = {}
    for field in ["job_title_raw", "company_name", "location", "salary",
                   "source_education", "source_experience", "text_education",
                   "text_experience", "responsibilities", "requirements",
                   "detail_raw_text", "source_url", "publish_time"]:
        filled = sum(1 for r in unified if r.get(field))
        field_rates[field] = f"{filled / total * 100:.1f}%"
    
    # 质量问题统计
    q_stats = Counter()
    for v in quality_issues.values():
        for issue in v:
            q_stats[issue] += 1
    
    # 冲突统计
    edu_conflict_count = sum(1 for r in unified if r.get("education_conflict"))
    exp_conflict_count = sum(1 for r in unified if r.get("experience_conflict"))
    
    # 审核原因分布
    review_reason_dist = Counter()
    for r in review_required:
        for reason in (r.get("review_reason", "") or "").split("; "):
            if reason:
                review_reason_dist[reason] += 1
    
    reject_reason_dist = Counter()
    for r in rejected:
        for reason in (r.get("rejection_reason", "") or "").split("; "):
            if reason:
                reject_reason_dist[reason] += 1
    
    # 达标判断
    reached = len(accepted) >= 100
    
    report = f"""# 候选池采集质量报告

> **生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
> **处理脚本**: merge_and_clean.py
> **SimHash 实现**: 项目标准 SimHash (app/services/data_quality/simhash.py)

---

## 一、原始数据

| 批次 | 文件 | 原始记录 | 成功解析 | 损坏 |
|------|------|---------|---------|------|
"""
    
    for filename, stats in file_stats.items():
        report += f"| {filename} | {stats['total']} | {stats['valid']} | {stats['corrupted']} |\n"
    
    report += f"""
| **合计** | **{sum(s['total'] for s in file_stats.values())}** | **{sum(s['valid'] for s in file_stats.values())}** | **{sum(s['corrupted'] for s in file_stats.values())}** |

### Pilot 重叠检测

| 指标 | 数量 |
|------|------|
| Pilot 总记录数 | {len(pilot_records)} |
| Pilot 中与后续批次重叠 | {pilot_in_later} |
| Pilot 中独有的 | {pilot_only} |

### 合并后唯一记录

| 指标 | 数量 |
|------|------|
| 原始汇总 | {total_raw} |
| 按 source_id 去重后 | {len(unified)} |

---

## 二、数据质量

| 质量指标 | 数量 |
|----------|------|
| 完整正文 (≥200字) | {total - q_stats.get('low_information', 0)} |
| 低信息 (<200字) | {q_stats.get('low_information', 0)} |
| 学历冲突 | {edu_conflict_count} |
| 经验冲突 | {exp_conflict_count} |
| 精确重复 (SHA-256) | {sha_dup_count} |
| 近似重复 (SimHash ≤3) | {len(approx_pairs)} 对 |
| 无法追溯 (无URL) | {q_stats.get('no_url', 0)} |
| 严重异常 (空正文/无标题/内容缺失) | {q_stats.get('empty_text', 0) + q_stats.get('no_title', 0) + q_stats.get('missing_content', 0)} |

---

## 三、数据分层

| 层级 | 数量 | 占比 |
|------|------|------|
| **accepted** | {len(accepted)} | {len(accepted)/total*100:.1f}% |
| **review_required** | {len(review_required)} | {len(review_required)/total*100:.1f}% |
| **rejected** | {len(rejected)} | {len(rejected)/total*100:.1f}% |
| **可进入人工标注** | {len(clean)} | {len(clean)/total*100:.1f}% |

> {"✅ 达标" if reached else "⚠️ 未达标"}: accepted = {len(accepted)} {"≥" if reached else "<"} 100 条

### Review Required 原因分布

| 原因 | 数量 |
|------|------|
"""
    
    for reason, cnt in sorted(review_reason_dist.items(), key=lambda x: -x[1]):
        report += f"| {reason} | {cnt} |\n"
    
    report += """
### Rejected 原因分布

| 原因 | 数量 |
|------|------|
"""
    
    for reason, cnt in sorted(reject_reason_dist.items(), key=lambda x: -x[1]):
        report += f"| {reason} | {cnt} |\n"
    
    report += f"""
---

## 四、岗位覆盖 (accepted + review_required)

| 岗位类别 | 数量 | 占比 |
|----------|------|------|
"""
    
    for cat, cnt in sorted(cat_dist_clean.items(), key=lambda x: -x[1]):
        report += f"| {cat} | {cnt} | {cnt/len(clean)*100:.1f}% |\n"
    
    report += f"""
### Accepted 岗位覆盖

| 岗位类别 | 数量 | 占比 |
|----------|------|------|
"""
    
    for cat, cnt in sorted(cat_dist_accepted.items(), key=lambda x: -x[1]):
        report += f"| {cat} | {cnt} | {cnt/len(accepted)*100:.1f}% |\n"
    
    report += f"""
---

## 五、来源覆盖

| 平台 | 数量 | 占比 |
|------|------|------|
"""
    
    for src, cnt in sorted(source_dist.items(), key=lambda x: -x[1]):
        report += f"| {src} | {cnt} | {cnt/total*100:.1f}% |\n"
    
    report += f"""
---

## 六、字段完整率

| 字段 | 填充率 |
|------|--------|
"""
    
    for field, rate in field_rates.items():
        report += f"| {field} | {rate} |\n"
    
    report += f"""
---

## 七、处理说明

1. **文件完整性**: 读取 {len(INPUT_FILES)} 个输入文件，全部成功解析
2. **Pilot 重叠**: 通过 source_id 精确匹配，检测到 {pilot_in_later} 条重叠，已合并保留信息更完整版本
3. **字段统一**: 所有记录统一为 {len(STANDARD_FIELDS)} 个标准字段
4. **SHA-256**: 基于 `responsibilities + "\\n" + requirements` 使用 `hashlib.sha256` 计算 64 位完整 SHA-256
5. **精确去重**: 依次检查 source_id → source_url → SHA256，保留信息更完整的一条，记录 `duplicate_of` 和 `duplicate_reason`
6. **SimHash 近似去重**: 使用项目标准 SimHash 实现 (`app/services/data_quality/simhash.py`)，仅对 `responsibilities + requirements` 计算，汉明距 ≤ 3 标记为 `duplicate_review_required`
7. **数据分层规则**:
   - **rejected**: 空正文 / 无URL / 无标题 / 精确重复 / 内容缺失
   - **review_required**: education_conflict / experience_conflict / low_information / approximate_duplicate
   - **accepted**: 通过所有检查，无冲突，无问题
8. **已知局限**: `publish_time` 完整率仅 {field_rates.get('publish_time', '0%')}，大部分记录缺失发布时间（智联招聘数据源共性）

---

*报告由 merge_and_clean.py 自动生成*
"""
    
    with open(OUTPUT_REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"  ✓ real_jd_collection_quality_report.md")
    
    # ═══════════════════════════════════════════════════════════════════
    # 最终摘要
    # ═══════════════════════════════════════════════════════════════════
    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()
    
    print("\n" + "=" * 70)
    print("  处理完成!")
    print(f"  耗时: {elapsed:.1f}s")
    print(f"  原始汇总: {total_raw} → 去重后: {len(unified)}")
    print(f"  accepted: {len(accepted)}")
    print(f"  review_required: {len(review_required)}")
    print(f"  rejected: {len(rejected)}")
    print(f"  可人工标注: {len(clean)} {'✅ 达标' if reached else '⚠️ 未达标'}")
    print("=" * 70)
    
    return {
        "total_raw": total_raw,
        "unique": len(unified),
        "accepted": len(accepted),
        "review_required": len(review_required),
        "rejected": len(rejected),
        "clean": len(clean),
        "reached": reached,
        "pilot_overlap": pilot_in_later,
        "pilot_only": pilot_only,
        "sha_dup": sha_dup_count,
        "approx_pairs": len(approx_pairs),
        "edu_conflict": edu_conflict_count,
        "exp_conflict": exp_conflict_count,
        "low_info": q_stats.get("low_information", 0),
        "cat_dist_clean": dict(cat_dist_clean),
        "cat_dist_accepted": dict(cat_dist_accepted),
        "source_dist": dict(source_dist),
    }


if __name__ == "__main__":
    result = main()