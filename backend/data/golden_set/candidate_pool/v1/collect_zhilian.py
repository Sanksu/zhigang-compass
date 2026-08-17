#!/usr/bin/env python3
"""智联招聘 JD 采集脚本 - 测试和运维/DevOps 类别
使用方式: python collect_zhilian.py  （纯标准库，无需安装额外依赖）
"""

import json
import re
import hashlib
import time
import random
import ssl
import urllib.request
from datetime import datetime, timezone, timedelta

# ========== 已排除的 source_ids ==========
EXCLUDED = {
    "CCL1480117890J40845576605", "CC138117190J40879068905",
    "CCL1516918430J40789275606", "CC192921310J40831468409",
    "CC303218880J40962177002", "CC258760917J90250298000",
    "CC000544460J40670242116", "CC385622410J40787862106",
    "CCL1480117890J40603130605", "CC135794170J41002479902",
    "CC000283190J40710167711", "CC246691810J40831043903",
}

# ========== 搜索配置 ==========
SEARCHES = [
    ("测试工程师", "530"), ("测试工程师", "538"), ("测试工程师", "539"),
    ("软件测试", "530"), ("软件测试", "765"),
    ("自动化测试", "530"), ("自动化测试", "538"),
    ("QA工程师", "530"), ("QA工程师", "765"),
    ("运维工程师", "530"), ("运维工程师", "538"), ("运维工程师", "539"),
    ("DevOps", "530"), ("DevOps", "765"),
    ("SRE", "530"),
    ("系统运维", "530"), ("系统运维", "538"),
]

TZ = timezone(timedelta(hours=8))
OUTPUT_PATH = r"d:\du_yan\jiebang_guashuai_jingsai\zhigang-compass\backend\data\golden_set\candidate_pool\v1\batch_test_ops.json"

# API 常量
_V = "0.43240637"
CLIENT_ID = "63ce3555-d2f2-470a-80f4-8538cee76c41"


def gen_page_request_id():
    return f"{int(time.time() * 1000)}-{random.randint(100000, 999999)}"


def _make_request(url, data=None, headers=None, timeout=15):
    """通用 HTTP 请求"""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=headers or {}, data=data, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("gbk", errors="replace")
    except Exception as e:
        return None


def search_jobs(keyword, city_code, page=1, page_size=60):
    """通过智联 API 搜索职位 (POST)"""
    url = (
        f"https://fe-api.zhaopin.com/c/i/search/positions?_v={_V}"
        f"&x-zp-page-request-id={gen_page_request_id()}"
        f"&x-zp-client-id={CLIENT_ID}"
    )
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://www.zhaopin.com",
        "Referer": "https://www.zhaopin.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
        "x-zp-page-code": "4019",
        "x-zp-platform": "13",
        "x-zp-business-system": "1",
    }
    payload = json.dumps({
        "S_SOU_FULL_INDEX": keyword,
        "S_SOU_WORK_CITY": city_code,
        "order": 4,
        "pageIndex": page,
        "pageSize": page_size,
        "anonymous": 1,
        "eventScenario": "pcSearchedSouSearch",
        "platform": 13,
        "version": "0.0.0",
    }).encode("utf-8")

    html = _make_request(url, data=payload, headers=headers)
    if not html:
        return []
    try:
        data = json.loads(html)
        return data.get("data", {}).get("list", [])
    except json.JSONDecodeError:
        return []


def fetch_detail_html(source_id):
    """获取详情页 HTML"""
    url = f"https://www.zhaopin.com/jobdetail/{source_id}.htm"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    return _make_request(url, headers=headers)


def extract_initial_state(html):
    """从 HTML 中提取 __INITIAL_STATE__ JSON"""
    if not html:
        return None
    match = re.search(r'__INITIAL_STATE__\s*=\s*(\{[\s\S]*?\})\s*</script>', html)
    if match:
        return match.group(1)
    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{[\s\S]*?\});', html)
    if match:
        return match.group(1)
    return None


def extract_detail_from_html(html):
    """从详情页 HTML 提取信息"""
    info = {"description": None, "title": None, "companyName": None,
            "workCity": None, "salary": None, "eduLevel": None,
            "workingExp": None, "skillList": []}

    state_json = extract_initial_state(html)
    if not state_json:
        return info

    try:
        data = json.loads(state_json)
    except json.JSONDecodeError:
        return info

    def find_job_detail(obj):
        if isinstance(obj, dict):
            if "jobDetail" in obj:
                return obj["jobDetail"]
            if "detailedPosition" in obj:
                return obj
            for v in obj.values():
                result = find_job_detail(v)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = find_job_detail(item)
                if result:
                    return result
        return None

    job_detail = find_job_detail(data)
    if not job_detail:
        return info

    if "detailedPosition" in job_detail and isinstance(job_detail["detailedPosition"], dict):
        info["description"] = job_detail["detailedPosition"].get("description", "")
    elif "description" in job_detail:
        info["description"] = job_detail.get("description", "")

    pos_info = job_detail.get("positionInfo", job_detail)
    if isinstance(pos_info, dict):
        info["title"] = pos_info.get("title") or pos_info.get("positionName", "")
        info["companyName"] = pos_info.get("companyName") or pos_info.get("company", "")
        info["workCity"] = pos_info.get("workCity") or pos_info.get("city", "")
        info["salary"] = pos_info.get("salary", "")
        info["eduLevel"] = pos_info.get("eduLevel") or pos_info.get("education", "")
        info["workingExp"] = pos_info.get("workingExp") or pos_info.get("experience", "")
        info["skillList"] = pos_info.get("skillList", []) or []

    return info


def parse_responsibilities_requirements(description):
    """从正文中拆分岗位职责和任职要求"""
    if not description:
        return "", ""

    patterns = [
        (r'【岗位职责】\s*\n*(.*?)(?:【任职要求】|【任职资格】|【岗位要求】|【职位要求】)',
         r'【任职要求】\s*\n*(.*?)$'),
        (r'岗位职责[：:]\s*\n*(.*?)(?:任职要求|任职资格|岗位要求|职位要求)',
         r'任职要求[：:]\s*\n*(.*?)$'),
        (r'岗位职责[：:]\s*\n*(.*?)(?:任职要求|任职资格|岗位要求|职位要求)',
         r'任职资格[：:]\s*\n*(.*?)$'),
        (r'工作职责[：:]\s*\n*(.*?)(?:工作要求|任职要求|任职资格)',
         r'工作要求[：:]\s*\n*(.*?)$'),
        (r'工作职责[：:]\s*\n*(.*?)(?:工作要求|任职要求|任职资格)',
         r'任职要求[：:]\s*\n*(.*?)$'),
        (r'职责描述[：:]\s*\n*(.*?)(?:任职要求|任职资格)',
         r'任职要求[：:]\s*\n*(.*?)$'),
        (r'岗位职责[：:]\s*\n*(.*?)(?:任职要求|任职资格|岗位要求|职位要求)',
         r'职位要求[：:]\s*\n*(.*?)$'),
    ]

    for resp_pat, req_pat in patterns:
        resp_match = re.search(resp_pat, description, re.DOTALL | re.IGNORECASE)
        req_match = re.search(req_pat, description, re.DOTALL | re.IGNORECASE)
        if resp_match and req_match:
            return resp_match.group(1).strip(), req_match.group(1).strip()

    parts = re.split(r'任职要求[：:]|任职资格[：:]|岗位要求[：:]|职位要求[：:]', description, maxsplit=1)
    if len(parts) == 2:
        resp = re.sub(r'^(岗位职责|工作职责|职责描述)[：:]\s*', '', parts[0]).strip()
        return resp, parts[1].strip()

    return description.strip(), ""


def extract_text_education(text):
    for p in [
        r'(本科及以上学历|硕士及以上学历|本科及以上|硕士及以上|博士及以上)',
        r'(本科|硕士|博士|大专|高中)(?:及以上)?(?:学历|学位)',
        r'学历[要求：:]\s*(本科|硕士|博士|大专|高中)',
    ]:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return ""


def extract_text_experience(text):
    for p in [
        r'(\d+[年]以上).*?(?:工作)?经验',
        r'(\d+[-~]\d+年).*?(?:工作)?经验',
        r'经验[要求：:]\s*(\d+[年]以上|\d+[-~]\d+年)',
        r'(经验不限|应届生)',
    ]:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return ""


def check_education_conflict(ssr_edu, text_edu):
    if not ssr_edu or not text_edu:
        return False
    levels = {"博士": 5, "硕士": 4, "本科": 3, "大专": 2, "高中": 1, "学历不限": 0}
    s = levels.get(ssr_edu.replace("及以上", "").replace("学历", "").strip(), 0)
    t = levels.get(text_edu.replace("及以上", "").replace("学历", "").strip(), 0)
    return s != 0 and t != 0 and s != t


def check_experience_conflict(ssr_exp, text_exp):
    if not ssr_exp or not text_exp:
        return False
    s = ssr_exp.lower().replace("经验不限", "0").replace("应届生", "0")
    t = text_exp.lower().replace("经验不限", "0").replace("应届生", "0")
    return s.strip() != t.strip()


def main():
    print("=" * 60)
    print("智联招聘 JD 采集 - 测试/运维 DevOps 类别")
    print("=" * 60)

    # Step 1: 通过 API 搜索收集 source_ids
    all_source_ids = {}
    for keyword, city_code in SEARCHES:
        print(f"\n搜索: kw={keyword}, city={city_code}")
        jobs = search_jobs(keyword, city_code, page=1, page_size=60)
        print(f"  获取到 {len(jobs)} 条结果")

        for job in jobs:
            sid = job.get("number", "")
            if not sid or not sid.startswith("CC"):
                continue
            if sid in EXCLUDED:
                continue
            if sid in all_source_ids:
                continue
            all_source_ids[sid] = {
                "title": job.get("jobName") or job.get("title", ""),
                "city": job.get("city", ""),
                "salary": job.get("salary", ""),
                "experience": job.get("workingExp", ""),
                "education": job.get("eduLevel", ""),
                "company": job.get("company") or job.get("companyName", ""),
                "publishTime": job.get("publishTime", ""),
            }

        time.sleep(1.5)

    print(f"\n去重后共 {len(all_source_ids)} 个候选 source_id")

    if not all_source_ids:
        print("⚠️ 未获取到任何 source_id! API 可能需要更新参数。")
        # 保存空文件
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    # Step 2: 逐个访问详情页
    collected = []
    for i, (sid, info) in enumerate(all_source_ids.items()):
        if len(collected) >= 25:
            break

        print(f"\n[{len(collected)+1}/25] 详情页: {sid}")
        html = fetch_detail_html(sid)
        if not html:
            continue

        detail = extract_detail_from_html(html)
        description = detail.get("description", "")
        if not description:
            print(f"  未提取到正文，跳过")
            continue

        responsibilities, requirements = parse_responsibilities_requirements(description)
        detail_raw_text = responsibilities + "\n" + requirements

        if len(detail_raw_text.strip()) < 50:
            print(f"  正文太短({len(detail_raw_text)}字符)，跳过")
            continue

        text_edu = extract_text_education(description)
        text_exp = extract_text_experience(description)

        ssr_edu = detail.get("eduLevel", "") or info.get("education", "")
        ssr_exp = detail.get("workingExp", "") or info.get("experience", "")
        edu_conflict = check_education_conflict(ssr_edu, text_edu)
        exp_conflict = check_experience_conflict(ssr_exp, text_exp)

        record = {
            "source": "zhilian",
            "source_id": sid,
            "source_url": f"https://www.zhaopin.com/jobdetail/{sid}.htm",
            "job_title_raw": detail.get("title") or info.get("title", ""),
            "company_name": detail.get("companyName") or info.get("company", ""),
            "location": detail.get("workCity") or info.get("city", ""),
            "salary": detail.get("salary") or info.get("salary", ""),
            "source_education": ssr_edu,
            "source_experience": ssr_exp,
            "text_education": text_edu,
            "text_experience": text_exp,
            "education_conflict": edu_conflict,
            "experience_conflict": exp_conflict,
            "publish_time": info.get("publishTime", ""),
            "crawl_time": datetime.now(TZ).isoformat(),
            "responsibilities": responsibilities,
            "requirements": requirements,
            "detail_raw_text": detail_raw_text,
            "tags": detail.get("skillList", []),
            "_sha256": hashlib.sha256(detail_raw_text.encode("utf-8")).hexdigest(),
        }

        collected.append(record)
        print(f"  成功: {record['job_title_raw'][:40]} | {record['company_name'][:20]} | {len(detail_raw_text)}字符")
        time.sleep(1.5)

    # Step 3: 保存结果
    print(f"\n{'=' * 60}")
    print(f"采集完成! 共 {len(collected)} 条")

    if collected:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(collected, f, ensure_ascii=False, indent=2)

        print(f"\n结果已保存到: {OUTPUT_PATH}")
        print(f"\nSource ID 列表:")
        for r in collected:
            print(f"  {r['source_id']} - {r['job_title_raw'][:50]}")

        categories = {}
        for r in collected:
            title = r["job_title_raw"].lower()
            if any(kw in title for kw in ["测试", "test", "qa"]):
                cat = "测试"
            elif any(kw in title for kw in ["运维", "devops", "sre"]):
                cat = "运维/DevOps"
            else:
                cat = "其他"
            categories[cat] = categories.get(cat, 0) + 1

        print(f"\n类别统计:")
        for cat, count in categories.items():
            print(f"  {cat}: {count} 条")
    else:
        print("未采集到任何数据!")
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump([], f)


if __name__ == "__main__":
    main()