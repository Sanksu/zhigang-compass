"""构建 JD 黄金集 100 条（DA-M2-11 + DA-M3-05 提前）。

双数据源合并：
- BOSS 直聘列表页 API（boss_golden.jsonl）：skills 字段为结构化技能数据，质量高
- 智联招聘列表页（zhilian_golden.jsonl）：tags 含技能标签，需 normalize_skills 过滤

合并策略：Boss 数据全部纳入（质量优先），不足部分从智联补足，分层抽样保证多样性。

注意：
- 两个数据源的详情页正文均因反爬无法采集，raw_text 为 API/元数据字段拼接
- M3 可通过其他方式补真实 JD 正文

运行：
    cd backend && uv run python scripts/build_golden_set.py
"""

import json
import re
import sys
from pathlib import Path

# 让脚本在无 site-packages editable install 时也能找到 app 模块
_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("build_golden_set")

from app.services.extraction.dictionary import normalize_skill

# ── 路径 ──
_BOSS_INPUT = _BACKEND_DIR / "data" / "crawlers" / "output" / "boss_golden.jsonl"
_ZHILIAN_INPUT = _BACKEND_DIR / "data" / "crawlers" / "output" / "zhilian_golden.jsonl"
_OUTPUT_DIR = _BACKEND_DIR / "data" / "golden_set"
_OUTPUT_FILE = _OUTPUT_DIR / "jd_golden_100.jsonl"

# ── 抽样目标：100 条 ──
TARGET_COUNT = 100

# ── 经验分层抽样配额（100 条规模）──
EXPERIENCE_QUOTA = {
    "经验不限": 25,
    "1-3年": 25,
    "3-5年": 25,
    "5-10年": 20,
    "10年以上": 5,
}

# ── 学历分层抽样配额（100 条规模）──
EDUCATION_QUOTA = {
    "大专": 10,
    "本科": 60,
    "硕士": 20,
    "学历不限": 10,
}

# ── 岗位类型分类关键词（用于多样性筛选）──
POSITION_CATEGORIES = {
    "后端": ["后端", "Python", "Java", "Go", "C++", "服务端"],
    "前端": ["前端", "Vue", "React", "Web", "H5"],
    "算法": ["算法", "机器学习", "深度学习", "AI", "大模型", "NLP", "CV"],
    "数据": ["数据", "大数据", "数开", "数据挖掘", "BI"],
    "全栈": ["全栈"],
    "测试": ["测试", "QA"],
    "运维": ["运维", "DevOps", "SRE"],
}


def parse_experience(exp_text: str) -> dict:
    """解析经验要求文本为 {min_years, max_years}。

    智联格式：
    - "经验不限" → {0, None}
    - "1-3年" → {1, 3}
    - "3-5年" → {3, 5}
    - "5-10年" → {5, 10}
    - "10年以上" → {10, None}
    """
    if not exp_text or exp_text == "经验不限":
        return {"min_years": 0, "max_years": None}
    if exp_text == "10年以上":
        return {"min_years": 10, "max_years": None}
    m = re.match(r"(\d+)-(\d+)年", exp_text)
    if m:
        return {"min_years": int(m.group(1)), "max_years": int(m.group(2))}
    return {"min_years": 0, "max_years": None}


def normalize_title(title: str) -> str:
    """标准化岗位名：去除括号备注、统一常见岗位名。

    示例：
    - "Python 算法工程师（CV 与神经网络方向）" → "算法工程师"
    - "资深全栈开发工程师（Python/Vue）" → "资深全栈开发工程师"
    - "Java/Python/C/C++/数开/测试/算法/前端岗" → "软件开发工程师"
    """
    # 去除中文/英文括号及内容
    title = re.sub(r"[（(][^)）]*[)）]", "", title).strip()
    # 多技能组合岗位名（如 "Java/Python/C/C++/数开/测试/算法/前端岗"）→ 统一为"软件开发工程师"
    if "/" in title and any(kw in title for kw in ["岗", "工程师", "开发"]):
        if re.search(r"[a-zA-Z]+/[a-zA-Z]+", title):
            return "软件开发工程师"
    # 去除多余空白
    title = re.sub(r"\s+", " ", title).strip()
    return title


def normalize_skills(tags: list[str]) -> list[str]:
    """归一化技能标签：用 SKILL_ALIAS 词典 + 去除非技能标签。

    Boss 的 skills 字段含真实技能（如 Django/MySQL/Python），
    也可能含非技能标签（如"5天/周"、"3个月"、"经验不限"），需过滤。
    """
    # 过滤明显非技能的标签
    NON_SKILL_PATTERNS = {
        "前端开发", "后端开发", "通信/物联网/自动化", "嵌入式",
        "固定年终", "周末加班双薪",
        # Boss 特有的非技能标签
        "经验不限", "1-3年", "3-5年", "5-10年", "10年以上",
        # 08-14 审查：经验/校招类标签污染 gold_skills（jd_009=["1年以内"]、jd_076=["校招"]）
        "1年以内", "1年以上", "2年以内", "经验要求", "经验",
        "校招", "在校", "在校生", "应届", "应届生", "在校/应届",
        "24届", "25届", "26届", "社招", "秋招", "春招",
        "大专", "本科", "硕士", "博士", "学历不限",
        "5天/周", "4天/周", "3天/周", "6天/周",
        "3个月", "6个月", "9个月", "12个月",
        "Remote", "全职", "兼职", "实习",
    }
    skills = []
    seen = set()
    for tag in tags:
        if tag in NON_SKILL_PATTERNS:
            continue
        # 过滤含"/"的非技能标签（如"5天/周"、"3个月"）
        if "/" in tag and any(kw in tag for kw in ["天/", "月", "年/", "周"]):
            continue
        # 用词典归一化
        normalized = normalize_skill(tag)
        # 过滤过短或纯数字
        if len(normalized) < 2 or normalized.isdigit():
            continue
        key = normalized.lower()
        if key not in seen:
            seen.add(key)
            skills.append(normalized)
    return skills


def infer_core_duties(title: str, tags: list[str]) -> list[str]:
    """基于岗位名和技能标签推断核心职责（≤3 条）。

    M3 真实详情页采集后可替换为 JD 原文中的职责描述。
    """
    duties = []

    if any(kw in title for kw in ["算法", "机器学习", "深度学习", "AI", "大模型"]):
        duties.append("负责机器学习算法的设计、训练与优化")
        duties.append("参与模型工程化部署与性能调优")
    elif any(kw in title for kw in ["前端", "Web", "H5"]):
        duties.append("负责 Web 前端页面开发与交互实现")
        duties.append("与后端协作完成 API 对接与数据展示")
    elif any(kw in title for kw in ["后端", "Python", "Java", "Go", "服务端"]):
        duties.append("负责后端服务设计与开发")
        duties.append("参与系统架构设计与性能优化")
    elif any(kw in title for kw in ["数据", "大数据", "数开"]):
        duties.append("负责数据管道设计与开发")
        duties.append("参与数据建模与数据质量治理")
    elif any(kw in title for kw in ["全栈"]):
        duties.append("负责前后端全栈开发")
        duties.append("参与产品全生命周期技术支持")
    elif any(kw in title for kw in ["测试", "QA"]):
        duties.append("负责测试用例设计与自动化测试脚本开发")
        duties.append("参与缺陷跟踪与质量保障")
    elif any(kw in title for kw in ["运维", "DevOps", "SRE"]):
        duties.append("负责系统部署与运维自动化")
        duties.append("参与监控体系建设与故障响应")
    else:
        duties.append("负责相关软件系统的设计与开发")

    return duties[:3]


def parse_boss_raw_text(raw_text: str) -> dict:
    """解析 Boss API 返回的 raw_text JSON，提取结构化字段。

    Boss 的 raw_text 是 joblist.json 接口返回的单条 job JSON，
    含 skills/jobExperience/jobDegree/brandIndustry/welfareList 等字段。
    """
    if not raw_text:
        return {}
    try:
        return json.loads(raw_text) if isinstance(raw_text, str) else raw_text
    except json.JSONDecodeError:
        return {}


# BOSS 原始 job JSON 中的敏感字段（请求签名 / 招聘者 PII / GPS / 内部标识符）
_SENSITIVE_KEYS = frozenset({
    "securityId", "bossName", "bossAvatar", "bossCert", "encryptBossId",
    "bossTitle", "goldHunter", "bossOnline", "encryptJobId", "expectId",
    "lid", "encryptBrandId", "itemId", "gps", "contact",
})


def strip_sensitive_fields(raw_text: str) -> str:
    """从原始 raw_text JSON 中剥离敏感字段（签名/PII/GPS/标识符）。

    非 JSON 或解析失败时原样返回；剥离后序列化回 JSON 字符串。
    """
    if not raw_text:
        return ""
    try:
        obj = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
    except json.JSONDecodeError:
        return raw_text

    def _strip(node):
        if isinstance(node, dict):
            return {k: _strip(v) for k, v in node.items() if k not in _SENSITIVE_KEYS}
        if isinstance(node, list):
            return [_strip(v) for v in node]
        return node

    return json.dumps(_strip(obj), ensure_ascii=False)


def build_raw_text(item: dict, boss_detail: dict) -> str:
    """基于 Boss API 真实数据合成 raw_text。

    格式：模拟 JD 简要描述，含岗位名/公司/薪资/经验/学历/技能/行业/福利等。
    Boss 详情页正文因反爬无法采集，此处用 API 结构化字段拼接。
    """
    parts = [
        f"岗位名称：{item.get('title', '')}",
        f"公司：{item.get('company', '')}",
        f"工作地点：{item.get('location', '')}",
        f"薪资：{item.get('salary', '')}",
        f"经验要求：{item.get('experience', '') or boss_detail.get('jobExperience', '')}",
        f"学历要求：{item.get('education', '') or boss_detail.get('jobDegree', '')}",
    ]

    # Boss 结构化技能（从 raw_text JSON 解析）
    boss_skills = boss_detail.get("skills", [])
    if boss_skills:
        parts.append(f"技能要求：{', '.join(boss_skills)}")

    # 公司行业与规模
    industry = boss_detail.get("brandIndustry", "")
    scale = boss_detail.get("brandScaleName", "")
    if industry:
        parts.append(f"公司行业：{industry}")
    if scale:
        parts.append(f"公司规模：{scale}")

    # 福利标签
    welfare = boss_detail.get("welfareList", [])
    if welfare:
        parts.append(f"福利待遇：{', '.join(welfare)}")

    # 招聘者信息
    boss_title = boss_detail.get("bossTitle", "")
    if boss_title:
        parts.append(f"招聘者：{boss_detail.get('bossName', '')}（{boss_title}）")

    return "\n".join(parts)


def build_zhilian_raw_text(item: dict) -> str:
    """基于智联列表页真实元数据合成 raw_text。

    智联详情页反爬无法采集正文，此处用列表页字段拼接。
    """
    parts = [
        f"岗位名称：{item.get('title', '')}",
        f"公司：{item.get('company', '')}",
        f"工作地点：{item.get('location', '')}",
        f"薪资：{item.get('salary', '')}",
        f"经验要求：{item.get('experience', '')}",
        f"学历要求：{item.get('education', '')}",
    ]
    tags = item.get("tags", [])
    if tags:
        parts.append(f"技能标签：{', '.join(tags)}")
    return "\n".join(parts)


def classify_position(title: str) -> str:
    """将岗位名归类到 POSITION_CATEGORIES 中的某一类。"""
    for category, keywords in POSITION_CATEGORIES.items():
        if any(kw in title for kw in keywords):
            return category
    return "其他"


def stratified_sample(items: list[dict]) -> list[dict]:
    """分层抽样：保证经验/学历/岗位类型多样性，Boss 数据优先。

    策略：
    1. 预处理：从 Boss raw_text JSON 补全 experience/education
    2. 按经验分组，组内 Boss 数据排在前面（优先纳入）
    3. 每个经验组按配额抽样，组内尽量覆盖不同学历和岗位类型
    4. 不足配额时从其他组补足
    """
    # 预处理：从 Boss raw_text JSON 补全 experience/education 到 item 顶层
    for item in items:
        if item.get("source") == "boss":
            boss_detail = parse_boss_raw_text(item.get("raw_text", ""))
            if not item.get("experience"):
                item["experience"] = boss_detail.get("jobExperience", "")
            if not item.get("education"):
                item["education"] = boss_detail.get("jobDegree", "")

    # 按经验分组，组内 Boss 优先（stable sort 保留原顺序）
    by_exp: dict[str, list[dict]] = {exp: [] for exp in EXPERIENCE_QUOTA}
    for item in items:
        exp = item.get("experience", "经验不限") or "经验不限"
        if exp not in by_exp:
            exp = "经验不限"
        by_exp[exp].append(item)

    # 组内排序：Boss 优先
    for exp in by_exp:
        by_exp[exp].sort(key=lambda x: 0 if x.get("source") == "boss" else 1)

    selected: list[dict] = []
    used_ids: set[str] = set()

    # 第一轮：按经验配额抽样，每个经验组内尽量覆盖不同学历和岗位类型
    for exp, quota in EXPERIENCE_QUOTA.items():
        pool = by_exp[exp]
        for item in pool:
            uid = item.get("source_id", "") or item.get("_fingerprint", "")
            if uid in used_ids:
                continue
            edu = item.get("education", "")
            cat = classify_position(item.get("title", ""))
            # 同一 (学历, 岗位类型) 组合最多取 3 条（100 条规模放宽）
            count_same = sum(1 for s in selected
                             if s.get("education") == edu
                             and classify_position(s.get("title", "")) == cat)
            if count_same >= 3:
                continue
            selected.append(item)
            used_ids.add(uid)
            if len([s for s in selected if s.get("experience") == exp]) >= quota:
                break

    # 第二轮：若不足 TARGET_COUNT，从剩余池子补足（仍按 Boss 优先）
    if len(selected) < TARGET_COUNT:
        all_remaining = [item for item in items
                         if (item.get("source_id", "") or item.get("_fingerprint", "")) not in used_ids]
        all_remaining.sort(key=lambda x: 0 if x.get("source") == "boss" else 1)
        for item in all_remaining:
            if len(selected) >= TARGET_COUNT:
                break
            selected.append(item)
            used_ids.add(item.get("source_id", "") or item.get("_fingerprint", ""))

    # 第三轮：若超过 TARGET_COUNT，截断
    return selected[:TARGET_COUNT]


def build_golden_entry(idx: int, item: dict) -> dict:
    """将单条爬虫数据转为黄金集标注格式（保留原始字段 + 新增 gold_* 标注）。

    设计原则：黄金集必须保留所有原始爬虫字段，新增 gold_* 标注字段。
    - 原始字段：source/source_id/source_url/crawled_at/raw_text(原始)/is_desensitized/
      _fingerprint/title/company/location/salary/experience/
      education/description/requirements/post_date/tags
    - 标注字段：gold_title/gold_skills/gold_bonus_skills/gold_experience/
      gold_education/gold_core_duties
    - 评测输入：raw_text（合成文本，用于 LLM 抽取评测）
    - 原始 raw_text 保留在 original_raw_text 字段
    """
    title = item.get("title", "")
    tags = item.get("tags", [])
    experience = item.get("experience", "")
    education = item.get("education", "")
    source = item.get("source", "zhilian")
    # 入库前剥离敏感字段（签名/PII/GPS），避免黄金集随仓库分发泄露
    original_raw_text = strip_sensitive_fields(item.get("raw_text", ""))

    if source == "boss":
        # Boss 数据：从原始 raw_text JSON 解析结构化 skills 字段
        boss_detail = parse_boss_raw_text(original_raw_text)
        boss_skills = boss_detail.get("skills", [])
        raw_skills = boss_skills if boss_skills else tags
        gold_skills = normalize_skills(raw_skills)

        if not experience:
            experience = boss_detail.get("jobExperience", "")
        if not education:
            education = boss_detail.get("jobDegree", "")

        synthesized_text = build_raw_text(item, boss_detail)
    else:
        # 智联数据：tags 含技能标签，需 normalize_skills 过滤
        raw_skills = tags
        gold_skills = normalize_skills(tags)
        synthesized_text = build_zhilian_raw_text(item)

    # 保留所有原始爬虫字段 + 新增标注字段
    entry = {
        # ── 黄金集标识 ──
        "id": f"jd_{idx:03d}",
        # ── 原始爬虫字段（完整保留，可回溯）──
        "source": source,
        "source_id": item.get("source_id", ""),
        "source_url": item.get("source_url", ""),
        "crawled_at": item.get("crawled_at", ""),
        # original_raw_text 已统一过脱敏（strip_sensitive_fields），黄金集一律声明无 PII
        "is_desensitized": True,
        "_fingerprint": item.get("_fingerprint", ""),
        "title": title,
        "company": item.get("company", ""),
        "location": item.get("location", ""),
        "salary": item.get("salary", ""),
        "experience": experience,
        "education": education,
        "description": item.get("description", ""),
        "requirements": item.get("requirements", ""),
        "post_date": item.get("post_date", ""),
        "tags": tags,
        "original_raw_text": original_raw_text,
        # ── 评测输入（合成文本，用于 LLM 抽取）──
        "raw_text": synthesized_text,
        # ── 黄金集标注字段 ──
        "gold_title": normalize_title(title),
        "gold_skills": gold_skills,
        "gold_bonus_skills": [],
        "gold_experience": parse_experience(experience),
        "gold_education": education,
        "gold_core_duties": infer_core_duties(title, raw_skills),
    }
    return entry


def main() -> int:
    # 读取两个数据源
    items: list[dict] = []

    # Boss 数据（质量优先）
    if _BOSS_INPUT.exists():
        with _BOSS_INPUT.open(encoding="utf-8") as f:
            boss_items = [json.loads(line) for line in f if line.strip()]
        for it in boss_items:
            it["source"] = "boss"
        items.extend(boss_items)
        logger.info(f"[build_golden_set] Boss 数据 {len(boss_items)} 条")
    else:
        logger.error(f"[警告] Boss 数据文件不存在 {_BOSS_INPUT}")

    # 智联数据（补足）
    if _ZHILIAN_INPUT.exists():
        with _ZHILIAN_INPUT.open(encoding="utf-8") as f:
            zhilian_items = [json.loads(line) for line in f if line.strip()]
        for it in zhilian_items:
            it["source"] = "zhilian"
        items.extend(zhilian_items)
        logger.info(f"[build_golden_set] 智联数据 {len(zhilian_items)} 条")
    else:
        logger.error(f"[警告] 智联数据文件不存在 {_ZHILIAN_INPUT}")

    if not items:
        logger.error("错误：无可用数据源")
        return 1

    logger.info(f"[build_golden_set] 合计 {len(items)} 条")

    # 过滤无效数据：title 非空且 normalize_skills 后至少有 1 个真实技能
    def has_real_skill(it: dict) -> bool:
        if not it.get("title"):
            return False
        if it.get("source") == "boss":
            boss_detail = parse_boss_raw_text(it.get("raw_text", ""))
            boss_skills = boss_detail.get("skills", [])
            raw_skills = boss_skills if boss_skills else it.get("tags", [])
        else:
            raw_skills = it.get("tags", [])
        return len(normalize_skills(raw_skills)) > 0

    valid_items = [it for it in items if has_real_skill(it)]
    logger.info(f"[build_golden_set] 有效数据 {len(valid_items)} 条（至少 1 个真实技能）")
    logger.info(f"[build_golden_set] 丢弃 {len(items) - len(valid_items)} 条无技能数据")

    # 按数据源统计有效条数
    from collections import Counter
    source_dist = Counter(it["source"] for it in valid_items)
    logger.info(f"[build_golden_set] 有效数据源分布: {dict(source_dist)}")

    # 分层抽样
    selected = stratified_sample(valid_items)
    logger.info(f"[build_golden_set] 抽样 {len(selected)} 条")

    # 构造黄金集
    golden_entries = [build_golden_entry(i + 1, item) for i, item in enumerate(selected)]

    # 写入输出
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with _OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for entry in golden_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # 统计输出
    logger.info(f"[build_golden_set] 已写入 {_OUTPUT_FILE}")
    logger.info(f"[build_golden_set] 总条数: {len(golden_entries)}")

    # 多样性统计
    import collections
    source_dist = collections.Counter(e["source"] for e in golden_entries)
    exp_dist = collections.Counter(e["gold_experience"]["min_years"] for e in golden_entries)
    edu_dist = collections.Counter(e["gold_education"] for e in golden_entries)
    cat_dist = collections.Counter(classify_position(e["gold_title"]) for e in golden_entries)
    logger.info(f"  数据源分布: {dict(source_dist)}")
    logger.info(f"  经验分布(min_years): {dict(exp_dist)}")
    logger.info(f"  学历分布: {dict(edu_dist)}")
    logger.info(f"  岗位类型分布: {dict(cat_dist)}")

    # 技能覆盖统计
    all_skills = set()
    for e in golden_entries:
        all_skills.update(e["gold_skills"])
    logger.info(f"  覆盖技能数: {len(all_skills)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
