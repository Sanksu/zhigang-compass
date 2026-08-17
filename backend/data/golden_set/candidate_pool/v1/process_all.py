#!/usr/bin/env python3
"""
process_all.py — 合并、清洗、分类、质检并生成候选池最终输出

用法:
    cd backend/data/golden_set/candidate_pool/v1
    uv run python process_all.py

输入文件 (在 v1 目录下):
    1. batch_data_ai.json              — JSON数组, 30条 (data/AI/algorithm)
    2. batch_embedded_security.json    — JSON数组, 26条 (embedded/security/other)
    3. ../real_jd_batch_20260815.json  — JSON数组, 22条
    4. batch_backend_fullstack.jsonl  — JSONL, 30条 (backend/fullstack)
    5. batch_frontend_test_ops.jsonl  — JSONL, 30条 (frontend/testing/DevOps)

输出文件 (在 v1 目录下):
    1. real_jd_candidates_raw.jsonl
    2. real_jd_candidates_clean.jsonl
    3. real_jd_candidates_clean.csv
    4. real_jd_review_required.csv
    5. real_jd_rejected.csv
    6. real_jd_collection_quality_report.md
"""

import json
import hashlib
import csv
import os
import re
from collections import Counter, defaultdict
from datetime import datetime

# ─── 配置 ──────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_JSON = os.path.join(os.path.dirname(BASE_DIR), "real_jd_batch_20260815.json")

# Pilot 12 条 source_ids (需要排除)
PILOT_EXCLUDE = {
    "CCL1480117890J40845576605", "CC138117190J40879068905",
    "CCL1516918430J40789275606", "CC192921310J40831468409",
    "CC303218880J40962177002", "CC258760917J90250298000",
    "CC000544460J40670242116", "CC385622410J40787862106",
    "CCL1480117890J40603130605", "CC135794170J41002479902",
    "CC000283190J40710167711", "CC246691810J40831043903",
}

# ─── 辅助函数 ──────────────────────────────────────────────────────────

def compute_sha256(text: str) -> str:
    """计算文本的 SHA-256 哈希"""
    if not text:
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def extract_education(text: str) -> str:
    """从 responsibilities + requirements 文本中提取学历要求"""
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
    """从 responsibilities + requirements 文本中提取经验要求"""
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
    # 简单规则：如果source说"大专"但text要求"本科及以上"
    if "大专" in source_edu or "专科" in source_edu:
        if "本科" in text_edu or "硕士" in text_edu or "博士" in text_edu:
            return True
    if "高中" in source_edu:
        if "本科" in text_edu or "硕士" in text_edu:
            return True
    if "学历不限" in source_edu:
        if "本科" in text_edu or "硕士" in text_edu:
            return True
    if "本科" in source_edu and ("硕士及以上" in text_edu or "博士" in text_edu):
        # source说本科但text要求硕士及以上 -> 不一定是矛盾（source可能不精确）
        return False
    return False


def check_experience_conflict(source_exp: str, text_exp: str) -> bool:
    """检查 source_experience 与 text_experience 是否存在明显矛盾"""
    if not source_exp or not text_exp:
        return False
    # 如果source说"经验不限"但text明确要求经验年限
    if "经验不限" in source_exp or "无经验" in source_exp:
        exp_match = re.search(r'(\d+)', text_exp)
        if exp_match:
            years = int(exp_match.group(1))
            if years >= 1:
                return True
    return False


def classify_job(title: str, tags: list) -> str:
    """
    根据 job_title_raw 和 tags 自动分类到岗位类别
    """
    title_lower = (title or "").lower()
    tags_str = " ".join(tags or []).lower()

    # 算法
    if any(kw in title_lower for kw in ["算法", "大模型", "llm", "nlp", "cv", "机器学习",
                                          "深度学习", "强化学习", "推荐算法", "运筹优化",
                                          "agent", "智能体", "ai算法", "aigc", "多模态"]):
        if any(kw in title_lower for kw in ["大模型", "llm", "aigc", "agent", "智能体",
                                              "多模态", "rag", "ai算法"]):
            return "AI/大模型"
        return "算法"

    # AI/大模型
    if any(kw in title_lower for kw in ["大模型", "llm", "aigc", "agent", "智能体",
                                          "多模态", "rag"]):
        return "AI/大模型"
    if any(kw in tags_str for kw in ["大模型", "llm", "aigc", "agent", "智能体",
                                       "rag", "langchain", "langgraph"]):
        return "AI/大模型"

    # 算法 (fallback)
    if any(kw in tags_str for kw in ["算法", "机器学习", "深度学习", "推荐算法", "nlp", "cv"]):
        return "算法"

    # 后端开发
    backend_kw = ["java", "python", "go", "golang", "php", "c++", "node.js", "后端",
                  "spring", "django", "flask", "gin", "express", "后端开发"]
    if any(kw in title_lower for kw in backend_kw) and not any(kw in title_lower for kw in ["前端", "全栈", "测试", "运维", "数据", "算法", "嵌入", "安全"]):
        return "后端开发"

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

    # 后端开发 (fallback by tags)
    if any(kw in tags_str for kw in ["java", "spring", "python", "django", "go", "php"]):
        return "后端开发"

    # 前端开发 (fallback by tags)
    if any(kw in tags_str for kw in ["vue", "react", "javascript", "前端"]):
        return "前端开发"

    return "其他技术岗"


def simhash(text: str) -> int:
    """
    简化版 SimHash 实现 (64-bit)
    基于 responsibilities + requirements 文本
    """
    if not text:
        return 0

    v = [0] * 64
    # 简单的 n-gram 分词 (3-gram)
    for i in range(len(text) - 2):
        gram = text[i:i + 3]
        h = hash(gram)
        for j in range(64):
            if h & (1 << j):
                v[j] += 1
            else:
                v[j] -= 1

    result = 0
    for j in range(64):
        if v[j] > 0:
            result |= (1 << j)
    return result


def hamming_distance(a: int, b: int) -> int:
    """计算两个整数的 Hamming 距离"""
    return bin(a ^ b).count('1')


def normalize_parent_record(record: dict) -> dict:
    """
    将 parent 格式 (real_jd_batch_20260815.json) 的记录标准化为 v1 格式
    """
    responsibilities = record.get("responsibilities", "")
    requirements = record.get("requirements", "")
    detail_raw = responsibilities + "\n" + requirements if responsibilities or requirements else record.get("description", "")

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

    return {
        "source": record.get("source", "zhilian"),
        "source_id": record.get("source_id", ""),
        "source_url": record.get("source_url", ""),
        "job_title_raw": record.get("title", ""),
        "company_name": company_name.strip(),
        "location": location.strip(),
        "salary": record.get("salary", ""),
        "source_education": source_edu,
        "source_experience": source_exp,
        "text_education": text_edu,
        "text_experience": text_exp,
        "education_conflict": edu_conflict,
        "experience_conflict": exp_conflict,
        "publish_time": str(record.get("publish_time") or ""),
        "crawl_time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "responsibilities": responsibilities,
        "requirements": requirements,
        "detail_raw_text": detail_raw.strip(),
        "tags": record.get("skills", record.get("tags", [])),
        "_sha256": compute_sha256(detail_raw),
    }


# ─── SimHash 近似去重 ─────────────────────────────────────────────────

def find_approximate_duplicates(records: list) -> list:
    """
    基于 responsibilities + requirements 的 SimHash 近似重复检测
    返回: [(idx_a, idx_b, hamming_dist), ...] 的近似重复对列表
    """
    pairs = []
    simhashes = []
    for i, r in enumerate(records):
        text = (r.get("responsibilities", "") or "") + "\n" + (r.get("requirements", "") or "")
        sh = simhash(text)
        simhashes.append(sh)

    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            dist = hamming_distance(simhashes[i], simhashes[j])
            if dist <= 3:
                pairs.append((i, j, dist))

    return pairs


# ─── 主流程 ────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("JD 候选池处理流程")
    print("=" * 60)

    # ── 步骤 1: 读取所有文件并合并 ──
    print("\n[步骤 1] 读取所有输入文件...")
    all_records = []

    # 1a. batch_data_ai.json
    with open(os.path.join(BASE_DIR, "batch_data_ai.json"), "r", encoding="utf-8") as f:
        data = json.load(f)
        for r in data:
            all_records.append(r)
    print(f"  batch_data_ai.json: {len(data)} 条")

    # 1b. batch_embedded_security.json
    with open(os.path.join(BASE_DIR, "batch_embedded_security.json"), "r", encoding="utf-8") as f:
        data = json.load(f)
        for r in data:
            all_records.append(r)
    print(f"  batch_embedded_security.json: {len(data)} 条")

    # 1c. ../real_jd_batch_20260815.json (parent format)
    with open(PARENT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
        for r in data:
            normalized = normalize_parent_record(r)
            all_records.append(normalized)
    print(f"  real_jd_batch_20260815.json: {len(data)} 条 (已标准化)")

    # 1d. batch_backend_fullstack.jsonl
    count = 0
    with open(os.path.join(BASE_DIR, "batch_backend_fullstack.jsonl"), "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                all_records.append(r)
                count += 1
    print(f"  batch_backend_fullstack.jsonl: {count} 条")

    # 1e. batch_frontend_test_ops.jsonl
    count = 0
    with open(os.path.join(BASE_DIR, "batch_frontend_test_ops.jsonl"), "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                all_records.append(r)
                count += 1
    print(f"  batch_frontend_test_ops.jsonl: {count} 条")

    total_before = len(all_records)
    print(f"\n  合并后总计: {total_before} 条")

    # ── 排除 Pilot 12 条 ──
    before_exclude = len(all_records)
    all_records = [r for r in all_records if r.get("source_id", "") not in PILOT_EXCLUDE]
    excluded = before_exclude - len(all_records)
    print(f"  排除 Pilot 12 条: 实际排除 {excluded} 条, 剩余 {len(all_records)} 条")

    # ── 步骤 2: 修复 _sha256 ──
    print("\n[步骤 2] 修复 _sha256...")
    sha256_fixed = 0
    for r in all_records:
        old_sha = r.get("_sha256", "")
        detail = r.get("detail_raw_text", "")
        new_sha = compute_sha256(detail)
        if old_sha != new_sha:
            sha256_fixed += 1
        r["_sha256"] = new_sha
    print(f"  修复了 {sha256_fixed} 条记录的 _sha256")

    # ── 步骤 3: 修复派生字段 ──
    print("\n[步骤 3] 修复派生字段...")
    edu_updated = 0
    exp_updated = 0
    conflict_updated = 0
    for r in all_records:
        combined = (r.get("responsibilities", "") or "") + "\n" + (r.get("requirements", "") or "")

        # 修复 text_education
        old_edu = r.get("text_education", "")
        new_edu = extract_education(combined)
        if new_edu and not old_edu:
            r["text_education"] = new_edu
            edu_updated += 1

        # 修复 text_experience
        old_exp = r.get("text_experience", "")
        new_exp = extract_experience(combined)
        if new_exp and not old_exp:
            r["text_experience"] = new_exp
            exp_updated += 1

        # 重新检查冲突
        source_edu = r.get("source_education", "")
        source_exp = r.get("source_experience", "")
        new_edu_conflict = check_education_conflict(source_edu, r.get("text_education", ""))
        new_exp_conflict = check_experience_conflict(source_exp, r.get("text_experience", ""))

        if r.get("education_conflict") != new_edu_conflict:
            conflict_updated += 1
        if r.get("experience_conflict") != new_exp_conflict:
            conflict_updated += 1

        r["education_conflict"] = new_edu_conflict
        r["experience_conflict"] = new_exp_conflict

    print(f"  补充 text_education: {edu_updated} 条")
    print(f"  补充 text_experience: {exp_updated} 条")
    print(f"  修正冲突标记: {conflict_updated} 处")

    # ── 步骤 4: 岗位分类 ──
    print("\n[步骤 4] 岗位分类...")
    for r in all_records:
        r["category"] = classify_job(r.get("job_title_raw", ""), r.get("tags", []))

    cat_counts = Counter(r["category"] for r in all_records)
    for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {cnt}")

    # ── 步骤 5: 质量检查 ──
    print("\n[步骤 5] 质量检查...")

    quality_issues = defaultdict(list)  # source_id -> [issue labels]

    # 5.1 空正文检测
    for r in all_records:
        if not r.get("detail_raw_text", "").strip():
            quality_issues[r["source_id"]].append("empty_text")

    # 5.2 正文长度检测
    for r in all_records:
        if len(r.get("detail_raw_text", "")) < 200:
            quality_issues[r["source_id"]].append("low_information")

    # 5.3 岗位名称检测
    for r in all_records:
        if not r.get("job_title_raw", "").strip():
            quality_issues[r["source_id"]].append("no_title")

    # 5.4 responsibilities/requirements 完整性
    for r in all_records:
        if not r.get("responsibilities", "").strip() and not r.get("requirements", "").strip():
            quality_issues[r["source_id"]].append("missing_content")

    # 5.5 source_url 可追溯检查
    for r in all_records:
        if not r.get("source_url", "").strip():
            quality_issues[r["source_id"]].append("no_url")

    # 5.6 SHA-256 精确重复检查
    sha_map = defaultdict(list)
    for i, r in enumerate(all_records):
        sha_map[r["_sha256"]].append(i)
    for sha, indices in sha_map.items():
        if len(indices) > 1:
            for idx in indices:
                quality_issues[all_records[idx]["source_id"]].append("exact_duplicate")

    # 5.7 URL 重复检查
    url_map = defaultdict(list)
    for i, r in enumerate(all_records):
        url = r.get("source_url", "")
        if url:
            url_map[url].append(i)
    for url, indices in url_map.items():
        if len(indices) > 1:
            for idx in indices:
                quality_issues[all_records[idx]["source_id"]].append("url_duplicate")

    # 5.8 SimHash 近似重复检查
    approx_pairs = find_approximate_duplicates(all_records)
    for i, j, dist in approx_pairs:
        quality_issues[all_records[i]["source_id"]].append("approximate_duplicate")
        quality_issues[all_records[j]["source_id"]].append("approximate_duplicate")

    print(f"  空正文: {sum(1 for v in quality_issues.values() if 'empty_text' in v)}")
    print(f"  低信息: {sum(1 for v in quality_issues.values() if 'low_information' in v)}")
    print(f"  无标题: {sum(1 for v in quality_issues.values() if 'no_title' in v)}")
    print(f"  内容缺失: {sum(1 for v in quality_issues.values() if 'missing_content' in v)}")
    print(f"  无URL: {sum(1 for v in quality_issues.values() if 'no_url' in v)}")
    print(f"  精确重复: {sum(1 for v in quality_issues.values() if 'exact_duplicate' in v)}")
    print(f"  URL重复: {sum(1 for v in quality_issues.values() if 'url_duplicate' in v)}")
    print(f"  近似重复: {sum(1 for v in quality_issues.values() if 'approximate_duplicate' in v)}")

    # ── 步骤 6: 数据分层 ──
    print("\n[步骤 6] 数据分层...")

    accepted = []
    review_required = []
    rejected = []

    for r in all_records:
        sid = r["source_id"]
        issues = quality_issues.get(sid, [])

        # rejected 条件: 空正文、无URL、无标题、精确重复、URL重复
        if "empty_text" in issues or "no_url" in issues or "no_title" in issues or \
           "exact_duplicate" in issues or "url_duplicate" in issues:
            r["tier"] = "rejected"
            r["reject_reason"] = "; ".join(sorted(set(issues)))
            rejected.append(r)
            continue

        # review_required 条件: education_conflict, experience_conflict, low_information, approximate_duplicate
        if r.get("education_conflict") or r.get("experience_conflict") or \
           "low_information" in issues or "approximate_duplicate" in issues:
            r["tier"] = "review_required"
            review_reasons = []
            if r.get("education_conflict"):
                review_reasons.append("education_conflict")
            if r.get("experience_conflict"):
                review_reasons.append("experience_conflict")
            if "low_information" in issues:
                review_reasons.append("low_information")
            if "approximate_duplicate" in issues:
                review_reasons.append("approximate_duplicate")
            r["review_reason"] = "; ".join(review_reasons)
            review_required.append(r)
            continue

        # accepted
        r["tier"] = "accepted"
        accepted.append(r)

    print(f"  accepted: {len(accepted)}")
    print(f"  review_required: {len(review_required)}")
    print(f"  rejected: {len(rejected)}")

    # ── 步骤 7: 生成输出文件 ──
    print("\n[步骤 7] 生成输出文件...")

    def write_jsonl(records, path):
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 7.1 raw JSONL (全部)
    raw_path = os.path.join(BASE_DIR, "real_jd_candidates_raw.jsonl")
    write_jsonl(all_records, raw_path)
    print(f"  ✓ {raw_path} ({len(all_records)} 条)")

    # 7.2 clean JSONL (accepted + review_required)
    clean = accepted + review_required
    clean_jsonl_path = os.path.join(BASE_DIR, "real_jd_candidates_clean.jsonl")
    write_jsonl(clean, clean_jsonl_path)
    print(f"  ✓ {clean_jsonl_path} ({len(clean)} 条)")

    # 7.3 clean CSV
    csv_fields = [
        "source", "source_id", "source_url", "job_title_raw", "company_name",
        "location", "salary", "source_education", "source_experience",
        "text_education", "text_experience", "education_conflict",
        "experience_conflict", "category", "tier", "review_reason",
        "responsibilities", "requirements", "detail_raw_text",
        "tags", "_sha256", "publish_time", "crawl_time"
    ]
    clean_csv_path = os.path.join(BASE_DIR, "real_jd_candidates_clean.csv")
    with open(clean_csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction='ignore')
        writer.writeheader()
        for r in clean:
            # 将 tags 列表转为字符串
            row = dict(r)
            if isinstance(row.get("tags"), list):
                row["tags"] = "; ".join(row["tags"])
            writer.writerow(row)
    print(f"  ✓ {clean_csv_path} ({len(clean)} 条)")

    # 7.4 review_required CSV
    review_csv_path = os.path.join(BASE_DIR, "real_jd_review_required.csv")
    with open(review_csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction='ignore')
        writer.writeheader()
        for r in review_required:
            row = dict(r)
            if isinstance(row.get("tags"), list):
                row["tags"] = "; ".join(row["tags"])
            writer.writerow(row)
    print(f"  ✓ {review_csv_path} ({len(review_required)} 条)")

    # 7.5 rejected CSV
    reject_fields = csv_fields + ["reject_reason"]
    rejected_csv_path = os.path.join(BASE_DIR, "real_jd_rejected.csv")
    with open(rejected_csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=reject_fields, extrasaction='ignore')
        writer.writeheader()
        for r in rejected:
            row = dict(r)
            if isinstance(row.get("tags"), list):
                row["tags"] = "; ".join(row["tags"])
            writer.writerow(row)
    print(f"  ✓ {rejected_csv_path} ({len(rejected)} 条)")

    # ── 步骤 8: 质量报告 ──
    print("\n[步骤 8] 生成质量报告...")

    # 来源分布
    source_dist = Counter(r.get("source", "unknown") for r in all_records)

    # 岗位分布
    cat_dist = Counter(r["category"] for r in clean)

    # 字段完整率
    total = len(all_records)
    field_rates = {}
    for field in ["job_title_raw", "company_name", "location", "salary",
                   "source_education", "source_experience", "text_education",
                   "text_experience", "responsibilities", "requirements",
                   "detail_raw_text", "source_url", "publish_time"]:
        filled = sum(1 for r in all_records if r.get(field, ""))
        field_rates[field] = f"{filled / total * 100:.1f}%"

    # 质量问题统计
    q_stats = Counter()
    for v in quality_issues.values():
        for issue in v:
            q_stats[issue] += 1

    # 冲突统计
    edu_conflict_count = sum(1 for r in all_records if r.get("education_conflict"))
    exp_conflict_count = sum(1 for r in all_records if r.get("experience_conflict"))

    # 审核原因分布
    review_reason_dist = Counter(
        r.get("review_reason", "") for r in review_required
    )

    # 生成 Markdown 报告
    report = f"""# 候选池采集质量报告

> 生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
> 处理脚本: process_all.py

---

## 【采集总数】

| 层级 | 数量 | 占比 |
|------|------|------|
| 原始采集 | {total_before} | 100% |
| 排除 Pilot 12 条 | {excluded} | — |
| 有效采集 | {total} | 100% |
| **accepted** | {len(accepted)} | {len(accepted)/total*100:.1f}% |
| **review_required** | {len(review_required)} | {len(review_required)/total*100:.1f}% |
| **rejected** | {len(rejected)} | {len(rejected)/total*100:.1f}% |
| **最终可进入人工标注** | {len(clean)} | {len(clean)/total*100:.1f}% |

> {"✅ 达标" if len(clean) >= 100 else "⚠️ 未达标"}: 最终可进入人工标注数量 {len(clean)} 条，{"≥" if len(clean) >= 100 else "<"} 100 条。

---

## 【来源分布】

| 平台 | 数量 | 占比 |
|------|------|------|
"""

    for src, cnt in sorted(source_dist.items(), key=lambda x: -x[1]):
        report += f"| {src} | {cnt} | {cnt/total*100:.1f}% |\n"

    report += f"""
---

## 【岗位分布】

| 岗位类别 | 数量 | 占比 |
|----------|------|------|
"""

    for cat, cnt in sorted(cat_dist.items(), key=lambda x: -x[1]):
        report += f"| {cat} | {cnt} | {cnt/len(clean)*100:.1f}% |\n"

    report += f"""
---

## 【字段完整率】

| 字段 | 填充率 |
|------|--------|
"""

    for field, rate in field_rates.items():
        report += f"| {field} | {rate} |\n"

    report += f"""
---

## 【质量】

| 质量指标 | 数量 |
|----------|------|
| 空正文 | {q_stats.get('empty_text', 0)} |
| 低信息 (<200字符) | {q_stats.get('low_information', 0)} |
| 教育冲突 | {edu_conflict_count} |
| 经验冲突 | {exp_conflict_count} |
| 精确重复 (SHA-256) | {q_stats.get('exact_duplicate', 0)} |
| 近似重复 (SimHash≤3) | {q_stats.get('approximate_duplicate', 0)} |
| 无法追溯 (无URL) | {q_stats.get('no_url', 0)} |
| 无标题 | {q_stats.get('no_title', 0)} |
| 内容缺失 | {q_stats.get('missing_content', 0)} |

---

## 【Review Required 原因分布】

| 原因 | 数量 |
|------|------|
"""

    for reason, cnt in sorted(review_reason_dist.items(), key=lambda x: -x[1]):
        if reason:
            report += f"| {reason} | {cnt} |\n"

    report += f"""
---

## 【Rejected 原因分布】

| 原因 | 数量 |
|------|------|
"""

    reject_reason_dist = Counter(r.get("reject_reason", "") for r in rejected)
    for reason, cnt in sorted(reject_reason_dist.items(), key=lambda x: -x[1]):
        if reason:
            report += f"| {reason} | {cnt} |\n"

    report += f"""
---

## 【其他平台状态】

| 平台 | 状态 | 原因 |
|------|------|------|
| 智联招聘 (zhilian) | ✅ 可用 | 已采集 {source_dist.get('zhilian', 0)} 条 |
| BOSS直聘 | ❌ 不可用 | BOSS 直聘 2026 年加强了反爬机制，动态 token + 滑块验证码，批量采集受限 |
| 脉脉 | ❌ 不可用 | 脉脉职位信息以社交动态形式呈现，无标准 JD 结构，且需登录后可见 |
| Indeed | ❌ 不可用 | Indeed 中国站已关闭，国际站以英文职位为主，与中文岗位不匹配 |
| 猎聘 | ❌ 不可用 | 猎聘以猎头驱动为主，JD 多为付费隐藏，公开可爬取职位有限 |
| 拉勾网 | ❌ 已移除 | 拉勾网于 2026-08-01 关闭，从原 14 源中移除 |

---

## 【处理说明】

1. **SHA-256 修复**: 对所有记录使用 `hashlib.sha256(detail_raw_text.encode('utf-8')).hexdigest()` 重新计算
2. **派生字段**: 从 responsibilities + requirements 中自动提取学历和经验要求
3. **冲突检测**: 对比 source_education/experience 与 text_education/experience 的明显矛盾
4. **SimHash 近似重复**: 基于 responsibilities + requirements 文本，Hamming 距离 ≤ 3 判定为近似重复
5. **数据分层规则**:
   - **rejected**: 空正文 / 无URL / 无标题 / 精确重复 / URL重复
   - **review_required**: education_conflict / experience_conflict / low_information / approximate_duplicate
   - **accepted**: 通过所有检查，无冲突
6. **Pilot 排除**: 排除了 pilot 阶段的 12 条 source_ids

---

*报告由 process_all.py 自动生成*
"""

    report_path = os.path.join(BASE_DIR, "real_jd_collection_quality_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"  ✓ {report_path}")

    # ── 最终摘要 ──
    print("\n" + "=" * 60)
    print("处理完成!")
    print(f"  原始采集: {total_before} → 排除 Pilot: {excluded} → 有效: {total}")
    print(f"  accepted: {len(accepted)}")
    print(f"  review_required: {len(review_required)}")
    print(f"  rejected: {len(rejected)}")
    print(f"  可人工标注: {len(clean)} {'✅' if len(clean) >= 100 else '⚠️'}")
    print("=" * 60)


if __name__ == "__main__":
    main()