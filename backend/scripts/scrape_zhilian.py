"""
智联招聘 JD 采集脚本 - 前端开发/测试/DevOps 类别
采集搜索页 source_ids，再逐条访问详情页提取正文
"""
import json
import re
import hashlib
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

# ========== 配置 ==========
CST = timezone(timedelta(hours=8))

# 已有的 pilot source_ids（12条，需排除）
EXISTING_IDS = {
    "CCL1480117890J40845576605", "CC138117190J40879068905",
    "CCL1516918430J40789275606", "CC192921310J40831468409",
    "CC303218880J40962177002", "CC258760917J90250298000",
    "CC000544460J40670242116", "CC385622410J40787862106",
    "CCL1480117890J40603130605", "CC135794170J41002479902",
    "CC000283190J40710167711", "CC246691810J40831043903",
}

# 搜索配置：(关键词, 城市代码列表)
SEARCH_CONFIG = [
    ("前端开发", ["530", "538", "539"]),
    ("React", ["530", "765"]),
    ("Vue", ["530", "763"]),
    ("Web前端", ["530", "538"]),
    ("测试工程师", ["530", "538", "539"]),
    ("软件测试", ["530", "765"]),
    ("自动化测试", ["530", "538"]),
    ("运维工程师", ["530", "538", "539"]),
    ("DevOps", ["530", "765"]),
    ("SRE", ["530", "538"]),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 教育关键词
EDU_KEYWORDS = [
    "本科", "硕士", "博士", "大专", "中专", "高中", "学历不限",
    "全日制本科", "统招本科", "研究生", "学士", "硕士及以上",
    "本科及以上", "大专及以上", "本科或以上", "专科及以上",
]

# 经验关键词
EXP_KEYWORDS = [
    "经验不限", "不限经验", "应届", "在校",
    "1年", "2年", "3年", "4年", "5年", "6年", "7年", "8年", "9年", "10年",
    "一年", "两年", "三年", "四年", "五年", "六年", "七年", "八年", "九年", "十年",
]


def extract_initial_state(html: str) -> dict | None:
    """从 HTML 中提取 __INITIAL_STATE__ JSON"""
    # 方法1: 标准模式
    m = re.search(r"__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", html, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 方法2: 更宽松的模式
    m = re.search(r"__INITIAL_STATE__\s*=\s*(\{.+?\});\s*</script>", html, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 方法3: 更宽松 - 找到 script 标签中的内容
    for m in re.finditer(r"<script[^>]*>window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*;\s*</script>", html, re.DOTALL):
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
    return None


def find_cc_ids(obj, found: set):
    """递归查找所有以 CC 开头的字符串值（source_id）"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "number" and isinstance(v, str) and (v.startswith("CC") or v.startswith("CCL")):
                found.add(v)
            find_cc_ids(v, found)
    elif isinstance(obj, list):
        for item in obj:
            find_cc_ids(item, found)
    elif isinstance(obj, str):
        if (obj.startswith("CC") or obj.startswith("CCL")) and len(obj) > 15:
            found.add(obj)


def extract_job_info_from_state(state: dict, source_id: str) -> dict | None:
    """从搜索页 state 中提取职位基本信息"""
    # 递归查找包含 number 的对象
    def find_job_data(obj, sid):
        if isinstance(obj, dict):
            if obj.get("number") == sid:
                return obj
            for v in obj.values():
                result = find_job_data(v, sid)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = find_job_data(item, sid)
                if result:
                    return result
        return None

    return find_job_data(state, source_id)


def extract_text_education(text: str) -> str:
    """从正文中提取学历要求"""
    for kw in EDU_KEYWORDS:
        if kw in text:
            return kw
    return ""


def extract_text_experience(text: str) -> str:
    """从正文中提取经验要求"""
    # 优先匹配具体年份
    patterns = [
        r"(\d+年[以之]?[上内]?[，,。.\s]?)",
        r"([一二三四五六七八九十]年[以之]?[上内]?[，,。.\s]?)",
        r"(\d+年以上[相]?关?[工经]?[作验]?)",
        r"(经验不限)",
        r"(应届)",
        r"(在校[生]?)",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip("，,。.")
    return ""


def split_responsibilities_requirements(text: str):
    """按标题拆分职责和任职要求"""
    # 常见的标题分隔符
    split_patterns = [
        r"【任职要求】",
        r"【任职资格】",
        r"【岗位要求】",
        r"【职位要求】",
        r"【工作要求】",
        r"【能力要求】",
        r"任职要求[：:]",
        r"任职资格[：:]",
        r"岗位要求[：:]",
        r"职位要求[：:]",
        r"工作要求[：:]",
        r"能力要求[：:]",
        r"资格要求[：:]",
        r"招聘要求[：:]",
        r"应聘要求[：:]",
        r"岗位资格[：:]",
    ]

    # 职责标题
    resp_patterns = [
        r"【岗位职责】",
        r"【工作职责】",
        r"【职位描述】",
        r"【工作内容】",
        r"【职责描述】",
        r"【岗位描述】",
        r"【工作描述】",
        r"岗位职责[：:]",
        r"工作职责[：:]",
        r"职位描述[：:]",
        r"工作内容[：:]",
        r"职责描述[：:]",
        r"岗位描述[：:]",
        r"工作描述[：:]",
    ]

    responsibilities = text
    requirements = ""

    # 先找职责部分
    for rp in resp_patterns:
        m = re.search(rp, text)
        if m:
            after_resp = text[m.end():]
            # 在职责后找任职要求
            for sp in split_patterns:
                m2 = re.search(sp, after_resp)
                if m2:
                    responsibilities = after_resp[:m2.start()].strip()
                    requirements = after_resp[m2.end():].strip()
                    break
            else:
                responsibilities = after_resp.strip()
            break
    else:
        # 没有职责标题，直接找任职要求拆分
        for sp in split_patterns:
            m = re.search(sp, text)
            if m:
                responsibilities = text[:m.start()].strip()
                requirements = text[m.end():].strip()
                break

    return responsibilities, requirements


def check_education_conflict(source_edu: str, text_edu: str) -> bool:
    """检查学历是否冲突"""
    if not source_edu or not text_edu:
        return False
    # 简化检查：如果来源学历和正文学历不一致
    edu_map = {
        "初中": 1, "中专": 2, "高中": 3, "中技": 3,
        "大专": 4, "本科": 5, "硕士": 6, "博士": 7, "MBA": 6,
    }
    # 提取数字等级
    def get_level(s):
        for k, v in edu_map.items():
            if k in s:
                return v
        return 0

    sl = get_level(source_edu)
    tl = get_level(text_edu)
    if sl and tl and abs(sl - tl) >= 2:
        return True
    return False


def check_experience_conflict(source_exp: str, text_exp: str) -> bool:
    """检查经验是否冲突"""
    if not source_exp or not text_exp:
        return False
    # 提取数字
    def get_years(s):
        nums = re.findall(r"(\d+)", s)
        if nums:
            return int(nums[0])
        return -1

    sy = get_years(source_exp)
    ty = get_years(text_exp)
    if sy >= 0 and ty >= 0 and abs(sy - ty) >= 2:
        return True
    return False


async def fetch_search_page(client: httpx.AsyncClient, keyword: str, city_code: str) -> list[dict]:
    """获取搜索结果页，返回 source_id 列表及基本信息"""
    url = f"https://sou.zhaopin.com/?jl={city_code}&kw={keyword}&pn=1"
    results = []

    try:
        resp = await client.get(url, timeout=30.0)
        if resp.status_code != 200:
            print(f"  [搜索] {keyword} 城市{city_code} HTTP {resp.status_code}")
            return results

        html = resp.text
        state = extract_initial_state(html)
        if not state:
            print(f"  [搜索] {keyword} 城市{city_code} 未找到 __INITIAL_STATE__")
            # 尝试从 HTML 中直接找 CC ID
            cc_ids = set()
            for m in re.finditer(r'"(CCL?\d+[A-Z]\d+)"', html):
                cc_ids.add(m.group(1))
            for m in re.finditer(r'"(CCL?\d+)"', html):
                if len(m.group(1)) > 15:
                    cc_ids.add(m.group(1))
            if cc_ids:
                print(f"  [搜索] {keyword} 城市{city_code} 从HTML直接提取到 {len(cc_ids)} 个 CC ID")
                for cid in cc_ids:
                    results.append({"number": cid})
            return results

        # 递归提取 CC ID
        cc_ids = set()
        find_cc_ids(state, cc_ids)
        print(f"  [搜索] {keyword} 城市{city_code} 找到 {len(cc_ids)} 个 CC ID")

        for cid in cc_ids:
            info = extract_job_info_from_state(state, cid)
            if info:
                results.append(info)
            else:
                results.append({"number": cid})

    except Exception as e:
        print(f"  [搜索] {keyword} 城市{city_code} 错误: {e}")

    return results


async def fetch_detail_page(client: httpx.AsyncClient, source_id: str) -> dict | None:
    """获取详情页，提取 JD 正文"""
    url = f"https://www.zhaopin.com/jobdetail/{source_id}.htm"

    try:
        resp = await client.get(url, timeout=30.0)
        if resp.status_code != 200:
            print(f"    [详情] {source_id} HTTP {resp.status_code}")
            return None

        html = resp.text
        state = extract_initial_state(html)
        if not state:
            print(f"    [详情] {source_id} 未找到 __INITIAL_STATE__")
            return None

        # 提取 jobDetail
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

        job_detail = find_job_detail(state)
        if not job_detail:
            print(f"    [详情] {source_id} 未找到 jobDetail")
            return None

        # 提取 description
        description = ""
        if isinstance(job_detail, dict):
            # 尝试多种路径
            desc = job_detail.get("detailedPosition", {}).get("description", "")
            if not desc:
                desc = job_detail.get("description", "")
            if not desc:
                # 尝试从 state 中其他地方找
                def find_description(obj, depth=0):
                    if depth > 10:
                        return ""
                    if isinstance(obj, dict):
                        if "description" in obj and isinstance(obj["description"], str) and len(obj["description"]) > 50:
                            return obj["description"]
                        for v in obj.values():
                            r = find_description(v, depth + 1)
                            if r:
                                return r
                    elif isinstance(obj, list):
                        for item in obj:
                            r = find_description(item, depth + 1)
                            if r:
                                return r
                    return ""
                desc = find_description(state)

            description = desc

        if not description or len(description.strip()) < 20:
            print(f"    [详情] {source_id} 描述为空或太短")
            return None

        # 提取元数据
        title = ""
        company_name = ""
        work_city = ""
        salary = ""
        edu_level = ""
        working_exp = ""
        skill_list = []

        if isinstance(job_detail, dict):
            title = job_detail.get("positionTitle", "") or job_detail.get("title", "") or job_detail.get("jobName", "")
            company_name = job_detail.get("companyName", "") or job_detail.get("company", {}).get("name", "")
            work_city = job_detail.get("workCity", "") or job_detail.get("city", "") or job_detail.get("cityDisplay", "")
            salary = job_detail.get("salary", "") or job_detail.get("salary60", "") or job_detail.get("salaryDisplay", "")
            edu_level = job_detail.get("eduLevel", {}).get("name", "") if isinstance(job_detail.get("eduLevel"), dict) else job_detail.get("eduLevel", "")
            working_exp = job_detail.get("workingExp", {}).get("name", "") if isinstance(job_detail.get("workingExp"), dict) else job_detail.get("workingExp", "")
            # skillList
            sl = job_detail.get("skillList", [])
            if isinstance(sl, list):
                skill_list = [s.get("name", s) if isinstance(s, dict) else s for s in sl]

            # 如果上面没取到，深入查找
            if not title:
                def find_title(obj, depth=0):
                    if depth > 10:
                        return ""
                    if isinstance(obj, dict):
                        for k in ["positionTitle", "title", "jobName", "positionName"]:
                            if k in obj and isinstance(obj[k], str):
                                return obj[k]
                        for v in obj.values():
                            r = find_title(v, depth + 1)
                            if r:
                                return r
                    return ""
                title = find_title(job_detail)

            if not company_name:
                def find_company(obj, depth=0):
                    if depth > 10:
                        return ""
                    if isinstance(obj, dict):
                        if "companyName" in obj and isinstance(obj["companyName"], str):
                            return obj["companyName"]
                        if "company" in obj and isinstance(obj["company"], dict):
                            return obj["company"].get("name", "")
                        for v in obj.values():
                            r = find_company(v, depth + 1)
                            if r:
                                return r
                    return ""
                company_name = find_company(job_detail)

        return {
            "title": title,
            "companyName": company_name,
            "workCity": work_city,
            "salary": salary,
            "eduLevel": edu_level,
            "workingExp": working_exp,
            "skillList": skill_list,
            "description": description,
        }

    except Exception as e:
        print(f"    [详情] {source_id} 错误: {e}")
        return None


def build_record(source_id: str, detail: dict) -> dict | None:
    """构建 JD 记录"""
    description = detail.get("description", "")
    if not description:
        return None

    # 拆分职责和任职要求
    responsibilities, requirements = split_responsibilities_requirements(description)

    # detail_raw_text = responsibilities + "\n" + requirements
    detail_raw_text = responsibilities
    if requirements:
        detail_raw_text += "\n" + requirements

    # SHA-256 与统一审计公式对齐：恒为 SHA256(responsibilities + "\n" + requirements)。
    # requirements 为空时也带分隔换行（与 detail_raw_text 的无尾换行形式解耦），
    # 否则统一审计会把空 req 记录判为 SHA legacy（08-28 unified_jd_audit 口径）
    sha256 = hashlib.sha256((responsibilities + "\n" + requirements).encode("utf-8")).hexdigest()

    # 提取正文中的学历和经验
    text_edu = extract_text_education(description)
    text_exp = extract_text_experience(description)

    # 检查冲突
    edu_conflict = check_education_conflict(detail.get("eduLevel", ""), text_edu)
    exp_conflict = check_experience_conflict(detail.get("workingExp", ""), text_exp)

    # 构建标签
    tags = []
    skills = detail.get("skillList", [])
    if isinstance(skills, list):
        tags.extend(skills[:5])

    # 提取 location
    location = detail.get("workCity", "")

    # 爬取时间
    crawl_time = datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%S")

    record = {
        "source": "zhilian",
        "source_id": source_id,
        "source_url": f"https://www.zhaopin.com/jobdetail/{source_id}.htm",
        "job_title_raw": detail.get("title", ""),
        "company_name": detail.get("companyName", ""),
        "location": location,
        "salary": detail.get("salary", ""),
        "source_education": detail.get("eduLevel", ""),
        "source_experience": detail.get("workingExp", ""),
        "text_education": text_edu,
        "text_experience": text_exp,
        "education_conflict": edu_conflict,
        "experience_conflict": exp_conflict,
        "publish_time": "",
        "crawl_time": crawl_time,
        "responsibilities": responsibilities,
        "requirements": requirements,
        "detail_raw_text": detail_raw_text,
        "tags": tags,
        "_sha256": sha256,
    }

    return record


async def main():
    output_path = (
        Path(__file__).resolve().parents[1] / "data" / "golden_set" / "candidate_pool" / "v1"
    )
    output_path.mkdir(parents=True, exist_ok=True)
    output_file = output_path / "batch_frontend_test_ops.jsonl"

    limits = httpx.Limits(max_connections=5, max_keepalive_connections=5)
    timeout = httpx.Timeout(30.0, connect=15.0)

    async with httpx.AsyncClient(
        headers=HEADERS,
        limits=limits,
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        # ========== 阶段1：搜索 ==========
        print("=" * 60)
        print("阶段1: 搜索职位列表")
        print("=" * 60)

        all_source_ids = set()
        all_job_info = {}  # source_id -> info dict

        for keyword, city_codes in SEARCH_CONFIG:
            for city_code in city_codes:
                print(f"\n搜索: {keyword} (城市: {city_code})")
                results = await fetch_search_page(client, keyword, city_code)
                for info in results:
                    sid = info.get("number", "")
                    if sid and sid not in EXISTING_IDS:
                        all_source_ids.add(sid)
                        if sid not in all_job_info:
                            all_job_info[sid] = info
                # 短暂延迟避免被封
                await asyncio.sleep(0.5)

        print(f"\n\n共找到 {len(all_source_ids)} 个唯一 source_id（已排除 pilot 12条）")

        # ========== 阶段2：详情页 ==========
        print("\n" + "=" * 60)
        print("阶段2: 获取详情页")
        print("=" * 60)

        records = []
        source_ids_list = list(all_source_ids)
        # 限制最多处理 100 个，目标 30 条
        source_ids_list = source_ids_list[:100]

        for i, sid in enumerate(source_ids_list):
            if len(records) >= 30:
                print("\n已达到目标 30 条，停止采集")
                break

            print(f"\n[{i+1}/{len(source_ids_list)}] {sid}")
            detail = await fetch_detail_page(client, sid)

            if not detail:
                continue

            record = build_record(sid, detail)
            if record and record.get("detail_raw_text"):
                records.append(record)
                print(f"  ✓ 成功: {record['job_title_raw'][:40]} | {record['location']} | {record['salary']}")
            else:
                print("  ✗ 跳过: 正文为空")

            await asyncio.sleep(0.3)

        # ========== 阶段3：保存 ==========
        print("\n" + "=" * 60)
        print("阶段3: 保存结果")
        print("=" * 60)

        with open(output_file, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # 统计
        category_counts = {}
        for r in records:
            title = r.get("job_title_raw", "")
            if any(kw in title for kw in ["前端", "React", "Vue", "Web", "H5", "网页", "JS"]):
                cat = "前端开发"
            elif any(kw in title for kw in ["测试", "Test", "QA", "质量"]):
                cat = "测试"
            elif any(kw in title for kw in ["运维", "DevOps", "SRE", "运营"]):
                cat = "运维/DevOps"
            else:
                cat = "其他"
            category_counts[cat] = category_counts.get(cat, 0) + 1

        print(f"\n保存到: {output_file}")
        print(f"成功采集: {len(records)} 条")
        print("各类别数量:")
        for cat, count in sorted(category_counts.items()):
            print(f"  {cat}: {count} 条")

        # 输出教育/经验冲突统计
        edu_conflicts = sum(1 for r in records if r.get("education_conflict"))
        exp_conflicts = sum(1 for r in records if r.get("experience_conflict"))
        print(f"\n学历冲突: {edu_conflicts} 条")
        print(f"经验冲突: {exp_conflicts} 条")

        return records, category_counts


if __name__ == "__main__":
    records, category_counts = asyncio.run(main())