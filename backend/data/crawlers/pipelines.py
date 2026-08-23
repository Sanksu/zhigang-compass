"""Scrapy Pipeline：数据清洗与入库路由。

链路：Item → CleaningPipeline(去重指纹+脱敏+文本标准化)
    → PostgresPipeline(upsert 到 raw 表)
    → [后续] LLM 抽取服务消费 raw 表 → 图谱写入服务 → Neo4j
"""

import hashlib
import math
import re
from datetime import date, datetime, timedelta
from typing import Optional

from scrapy.exceptions import DropItem
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from crawlers.items import CommunityTrendItem, CourseItem, JobItem, PaperItem


# 所有需要文本标准化的字段（跨 Item 类型）
_TEXT_FIELDS = (
    # JobItem
    "description", "requirements",
    # PaperItem
    "abstract",
)

# ── 实习/兼职岗位源头过滤 ──
# 岗位标题或平台标签（job_type 常写入 tags）命中即拦截，不落 jd_raw，
# 避免下游抽取/聚合处理无效岗位。英文词边界设计：
#   \bintern(?:ship)?s?\b 不匹配 internal/internet/international；
#   part[\s_\-]?time 覆盖 part-time / part time / parttime / PART_TIME。
_EMPLOYMENT_INTERN_RE = re.compile(r"\bintern(?:ship)?s?\b", re.IGNORECASE)
_EMPLOYMENT_PARTTIME_RE = re.compile(r"\bpart[\s_\-]?time\b", re.IGNORECASE)
_EMPLOYMENT_INTERN_CN = "实习"    # 含"实习生"
_EMPLOYMENT_PARTTIME_CN = "兼职"


# ── 非正常岗位源头过滤（2026-08-07 补充）──
# 批量聚合招聘帖：标题为技术栈列表（Java/C++/Python/前端/测试…）或招聘话术，
# 无明确岗位名——不是单一岗位，LLM 抽取/聚合会产生污染岗位（如"系统""解决方案"）。
# 判定：标题不含强岗位词（中英）+ （含 ≥2 个技术栈分隔符 或 批量招募话术）。
# 强岗位词须含英文岗位词（Engineer/Developer/Analyst 等）：国际源标题为英文，
# 否则 "Software Engineer, AI/ML" 会被"分隔符+无中文岗位词"误判为聚合帖。
# "前端"不进表：聚合帖常列"前端"项，"前端"单独出现多为简写岗位名（靠
# "全栈/工程师"等词保底正常岗，如"前端全栈工程师"）。
_POS_NAME_STRONG_RE = re.compile(
    r"工程师|开发|分析|设计|运营|经理|架构|研究|评测|技术|主管|顾问|讲师|教师|"
    r"销售|助理|专家|咨询|运维|实施|产品|专员|管培|储备|总监|教练|审核|主播|护士|"
    r"客服|财务|人事|市场|编辑|司机|厨师|保安|保洁|前台|全栈|算法|数据|后端"
    r"|标注|训练|翻译|美工|剪辑|修图|撰写|美编|策划|摄影|摄像|主持|文员|秘书"
    r"|软件测试|测试开发|测试工程师"  # "测试"单独是聚合帖列表项，不进表；带岗位前缀/后缀的组合词保正常岗
    r"|Engineer|Developer|Analyst|Manager|Architect|Scientist|Researcher|Specialist|"
    r"Consultant|Director|Officer|Operator|Designer|Editor|Nurse|Driver|Chef|Trainer|"
    r"Coordinator|Administrator|Intern|Trainee|Tester|Lead|Principal|Staff"
)
# 技术栈分隔符（含中文顿号/斜杠/加号变体/半角逗号）
_STACK_SEP_RE = re.compile(r"[/、＋＋+，,·]")
# 批量招募话术（无岗位名时出现这些词 → 聚合帖）
_RECRUIT_SPAM_RE = re.compile(
    r"接受应届|无经验|不限语言|线上面试|可投|高薪|急招|双休|16薪|14薪|六险一金|"
    r"拒绝内卷|月入过万|核心项目|年终奖|导师带教|长期项目"
)


def _invalid_job_reason(item) -> str | None:
    """非正常岗位（批量聚合帖/话术帖）拦截原因；未命中返回 None。

    与 _employment_reason 并列：实习/兼职在标题判定，聚合帖在标题+技术栈
    分隔+话术判定。正常岗位（含岗位名强词，如"Java开发工程师（接受应届）"）
    不拦截，避免误杀。
    """
    title = str(item.get("title") or "")
    if not title:
        return None
    if _POS_NAME_STRONG_RE.search(title):
        return None
    seps = len(_STACK_SEP_RE.findall(title))
    if seps >= 2 or _RECRUIT_SPAM_RE.search(title):
        return "批量聚合帖（无岗位名）"
    return None


def _employment_reason(item) -> str | None:
    """实习/兼职岗位的拦截原因；未命中返回 None。

    标题为岗位名，中文"实习/兼职"与英文 intern/part-time 均检查；
    tags 多为平台 job_type 标签（INTERNSHIP / PART_TIME / parttime），
    仅查英文词——中文标签（如智联"金融分析大学生实习"）是技能/招聘对象
    标签而非就业类型，避免误拦截正式岗位。
    """
    title = str(item.get("title") or "")
    tags_text = " ".join(
        str(t) for t in (item.get("tags") or []) if isinstance(t, str)
    )
    if _EMPLOYMENT_INTERN_CN in title or _EMPLOYMENT_INTERN_RE.search(title):
        return "实习岗位"
    if _EMPLOYMENT_PARTTIME_CN in title or _EMPLOYMENT_PARTTIME_RE.search(title):
        return "兼职岗位"
    # 英文 job_type 标签（中文标签含招聘对象描述，不以此判定）
    if _EMPLOYMENT_INTERN_RE.search(tags_text):
        return "实习岗位"
    if _EMPLOYMENT_PARTTIME_RE.search(tags_text):
        return "兼职岗位"
    return None


# ── 质量过滤（设计文档 §4.2：长度过滤 → 核心词检测 → 质量评分 → 时效加权）──
MIN_JD_LENGTH = 50            # JD 全文长度 < 50 字丢弃
QUALITY_REVIEW_THRESHOLD = 0.6  # 质量评分 < 0.6 入人工复核
_JD_TEXT_LENGTH_FULL = 200    # 文本长度维度满分基准（200 字）
_JD_KEYWORD_FULL = 3          # 核心词命中维度满分基准（3 词）

# 招聘核心词集（中英）：JD 文本命中任一即视为含招聘意图，
# 供核心词检测维度使用（质量评分组成之一）。
_JD_CORE_KEYWORDS = (
    "负责", "职责", "要求", "经验", "熟悉", "掌握", "具备", "优先",
    "开发", "设计", "维护", "测试", "交付", "能力", "技能",
    "responsib", "requir", "experience", "skill", "develop",
    "design", "build", "manage", "support", "ability",
)

# 格式规范：控制字符或同一字符连续 20+（乱码/爬虫噪声）视为格式异常
_BAD_FORMAT_RE = re.compile(r"([^\W\d_])\1{19,}|[\x00-\x08\x0b\x0c\x0e-\x1f]")


def jd_quality_breakdown(
    title: str,
    description: str = "",
    requirements: str = "",
    company: str = "",
    location: str = "",
    salary: str = "",
    raw_text: str = "",
) -> dict:
    """质量评分分量明细（score 及各维度分数/输入）。

    拆出明细供日志排查评分异常（哪一维拖低/命中哪些核心词），
    jd_quality_score 只取 score，避免重复计算。
    """
    # raw_text 计入文本长度/核心词/格式维度（与长度过滤口径一致），
    # 但不计入字段完整度——它是原始抓取转储而非结构化字段
    text = " ".join(filter(None, (title, description, requirements, raw_text)))
    fields = [title, company, location, salary, description, requirements]
    completeness = sum(1 for f in fields if str(f or "").strip()) / len(fields)
    length_score = min(len(text) / _JD_TEXT_LENGTH_FULL, 1.0)
    hits = [kw for kw in _JD_CORE_KEYWORDS if kw in text]
    keyword_score = min(len(hits) / _JD_KEYWORD_FULL, 1.0)
    format_score = 0.5 if _BAD_FORMAT_RE.search(text) else 1.0
    return {
        "text_len": len(text),
        "completeness": round(completeness, 3),
        "length_score": round(length_score, 3),
        "keyword_hits": hits,
        "keyword_score": round(keyword_score, 3),
        "format_score": format_score,
        "score": round(
            0.4 * completeness
            + 0.3 * length_score
            + 0.2 * keyword_score
            + 0.1 * format_score,
            3,
        ),
    }


def jd_quality_score(
    title: str,
    description: str = "",
    requirements: str = "",
    company: str = "",
    location: str = "",
    salary: str = "",
    raw_text: str = "",
) -> float:
    """JD 质量评分（设计文档 §4.2：字段完整度 0.4 + 文本长度 0.3 + 核心词 0.2 + 格式规范 0.1）。"""
    return jd_quality_breakdown(
        title, description, requirements, company, location, salary, raw_text
    )["score"]


def _parse_date(value) -> Optional[date]:
    """宽松解析日期：ISO8601 或 YYYY-MM-DD 前缀；失败返回 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value))
        if m:
            return date(*map(int, m.groups()))
        return None


def jd_decay_breakdown(publish_time, today: Optional[date] = None) -> dict:
    """时效加权分量明细（解析出的发布日期/距今天数/衰减权重）。

    拆出明细供日志排查时效异常（post_date 解析失败 vs 超 30 天衰减），
    jd_decay_weight 只取权重，避免重复计算。
    """
    pub = _parse_date(publish_time)
    if pub is None:
        return {"publish_date": None, "days_ago": None, "decay_weight": 1.0}
    days = (today or date.today()) - pub
    days_ago = days.days
    weight = 1.0 if days_ago <= 30 else math.exp(-0.01 * (days_ago - 30))
    return {
        "publish_date": pub.isoformat(),
        "days_ago": days_ago,
        "decay_weight": round(weight, 3),
    }


def jd_decay_weight(publish_time, today: Optional[date] = None) -> float:
    """时效加权（设计文档 §4.2）：≤30 天 1.0，>30 天 exp(-0.01×(days_ago-30))。

    无发布信息返回 1.0（不惩罚无日期标注的采集数据）。
    """
    return jd_decay_breakdown(publish_time, today)["decay_weight"]


# ── post_date 归一化 ──
# 各平台 post_date 格式不一：zhilian "2026-08-09 00:27:24"（空格分隔 datetime）、
# indeed/linkedin "2026-08-06"（ISO date）、glassdoor "3d"/"30d+"/"2w"（相对天数）。
# 相对时间若不转绝对日期，下游 _publish_date（tasks.py）与 SQL 解析均失败，
# 导致时滞检测/新鲜度审计把合法数据误判为无发布日期。故清洗层统一归一化。
_RELATIVE_DATE_RE = re.compile(r"(\d+)\s*([dw])\+?", re.IGNORECASE)


def normalize_post_date(value, today: Optional[date] = None) -> str:
    """post_date 归一化：相对时间（glassdoor 等）转绝对日期，其余保留。

    相对时间格式：`3d`/`30d+`（N 天前）、`2w`（N 周前）、Today/Yesterday/中文。
    已是可解析日期（YYYY-MM-DD / 空格分隔 datetime / ISO8601）原样返回；
    无法解析时原样返回（不强行改写，避免写入错误日期）。
    """
    if not value:
        return ""
    raw = str(value).strip()
    today = today or date.today()

    # 已是可解析日期（_parse_date 兼容 zhilian 空格分隔格式）→ 保留
    if _parse_date(raw) is not None:
        return raw

    low = raw.lower()
    if low in ("today", "今天"):
        return today.isoformat()
    if low in ("yesterday", "昨天"):
        return (today - timedelta(days=1)).isoformat()

    m = _RELATIVE_DATE_RE.match(low)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        days = n if unit == "d" else n * 7
        return (today - timedelta(days=days)).isoformat()

    return raw


class CleaningPipeline:
    """基础清洗：长度过滤 + 去重指纹 + 文本标准化 + 脱敏标记 + 实习/兼职岗位源头过滤。

    对齐设计文档 §4.2 管线（raw_JD → 长度过滤 → 核心词检测 → 质量评分
    → 去重 → 时效加权 → 结构化输出）：
    - 实习/兼职岗位源头拦截（最早，避免无效岗位进入下游）
    - 全文长度 < MIN_JD_LENGTH 丢弃
    - 质量评分写入 snapshot.quality（核心词检测为评分维度之一），< 0.6 标 needs_review
    - 时效加权写入 snapshot.decay_weight
    """

    # 边界 (?<!\d)/(?!\d) 防止误伤长数字 ID（如 19 位 source_id）中的子串
    PII_PATTERNS = [
        (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "ID_CARD"),  # 18 位身份证优先（避免被 PHONE 子串误匹配）
        (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "PHONE"),
        (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "EMAIL"),
    ]

    def __init__(self):
        self.crawler = None
        self._filtered_count = 0  # 本次采集拦截的实习/兼职岗位数（close_spider 汇总日志）

    @classmethod
    def from_crawler(cls, crawler):
        """scrapy 2.12+ 移除 pipeline 方法中的 spider 参数，经 crawler 访问 spider。"""
        pipe = cls()
        pipe.crawler = crawler
        return pipe

    def _spider(self):
        return self.crawler.spider if self.crawler is not None else None

    def process_item(self, item):
        # 实习/兼职 + 批量聚合帖源头拦截：在清洗阶段丢弃（不计算指纹、不落库），
        # 拦截日志保留 title/company 便于核对误杀
        if isinstance(item, JobItem):
            reason = _employment_reason(item) or _invalid_job_reason(item)
            if reason:
                self._filtered_count += 1
                spider = self._spider()
                if spider:
                    spider.logger.info(
                        "[岗位过滤] 拦截岗位 原因=%s source=%s title=%r company=%r",
                        reason,
                        item.get("source", ""),
                        item.get("title", ""),
                        item.get("company", ""),
                    )
                raise DropItem(f"{reason}: {item.get('title', '')}")

            # 长度过滤（设计文档 §4.2）：JD 全文 < 50 字丢弃（缺失正文的占位数据）
            full_text = " ".join(filter(None, (
                item.get("title", ""),
                item.get("description", ""),
                item.get("requirements", ""),
                item.get("raw_text", ""),
            )))
            if len(full_text) < MIN_JD_LENGTH:
                raise DropItem(
                    f"JD 文本过短（{len(full_text)} 字 < {MIN_JD_LENGTH}）: {item.get('title', '')}"
                )

        # 去重指纹：source + source_id 的 SHA256；source_id 缺失时回退 source_url
        # （避免不同记录指纹相同且按 (source, source_id) upsert 互相覆盖）
        if not item.get("source_id"):
            item["source_id"] = item.get("source_url", "") or item.get("title", "")
        item["_fingerprint"] = hashlib.sha256(
            f"{item.get('source', '')}:{item.get('source_id', '')}".encode()
        ).hexdigest()

        # PII 脱敏：title/description/requirements/raw_text 均可能含手机/邮箱/身份证
        # （08-15 中危修复：补 title 字段 + 课程/论文/社区 Item 的文本字段——
        # 此前仅 JobItem 三字段，标题含"李工138xxx"类联系方式会漏网），
        # 对所有 Item 类型统一脱敏，is_desensitized 标记
        if isinstance(item, JobItem):
            for field in ("title", "description", "requirements", "raw_text"):
                if item.get(field) and isinstance(item.get(field), str):
                    item[field] = self._desensitize(item[field])
            item["is_desensitized"] = True
        else:
            for field in ("title", "description", "raw_text"):
                if item.get(field) and isinstance(item.get(field), str):
                    item[field] = self._desensitize(item[field])
            item["is_desensitized"] = True

        # 语义指纹：JobItem 计算 SimHash（title+company+description），跨平台近似去重用。
        # 基于脱敏后文本计算，与落库形态一致。
        # 短文本（仅标题）单 token 变化会导致海明距过大，故包含 description 保证判定稳健
        if isinstance(item, JobItem):
            from app.services.data_quality.simhash import simhash64
            item["_simhash"] = simhash64(
                " ".join(filter(None, [
                    item.get("title", ""),
                    item.get("company", ""),
                    item.get("description", ""),
                    item.get("requirements", ""),
                ]))
            )

            # 质量评分 + 时效加权（设计文档 §4.2）：写入 snapshot 供下游与人工复核。
            # 核心词检测为评分维度之一；< 0.6 标 needs_review（不丢弃，进人工复核队列）
            # post_date 先归一化（glassdoor 相对时间 → 绝对日期），保证 decay 计算与
            # 下游 _publish_date/SQL 解析一致（§4.2 时效维度）
            item["post_date"] = normalize_post_date(item.get("post_date"))
            q = jd_quality_breakdown(
                title=str(item.get("title") or ""),
                description=str(item.get("description") or ""),
                requirements=str(item.get("requirements") or ""),
                company=str(item.get("company") or ""),
                location=str(item.get("location") or ""),
                salary=str(item.get("salary") or ""),
                raw_text=str(item.get("raw_text") or ""),
            )
            d = jd_decay_breakdown(item.get("post_date"))
            item["quality"] = q["score"]
            item["needs_review"] = q["score"] < QUALITY_REVIEW_THRESHOLD
            item["decay_weight"] = d["decay_weight"]

            # 详细日志（排查评分/时效异常）：记录各维度输入与分数，尤其核心词
            # 命中明细（空命中→keyword_score=0）与时效（post_date 解析失败→1.0）。
            # needs_review 命中时用 warning 级别，便于人工复核及时关注
            spider = self._spider()
            if spider:
                log_line = (
                    "[清洗质量] source=%s title=%r quality=%.3f needs_review=%s "
                    "| 完整度=%.2f 长度=%.2f(%d字) 核心词=%.2f(命中%d:%s) 格式=%.2f"
                    " | 时效=%.3f(发布=%s 距今=%s天)"
                )
                log_args = (
                    item.get("source", ""),
                    item.get("title", ""),
                    q["score"],
                    item["needs_review"],
                    q["completeness"],
                    q["length_score"],
                    q["text_len"],
                    q["keyword_score"],
                    len(q["keyword_hits"]),
                    "/".join(q["keyword_hits"]) or "-",
                    q["format_score"],
                    d["decay_weight"],
                    d["publish_date"],
                    d["days_ago"],
                )
                if item["needs_review"]:
                    spider.logger.warning(log_line, *log_args)
                else:
                    spider.logger.info(log_line, *log_args)

        # 文本标准化：对所有已知文本字段 strip
        for field in _TEXT_FIELDS:
            if item.get(field) and isinstance(item.get(field), str):
                item[field] = item[field].strip()
        if item.get("raw_text") and isinstance(item.get("raw_text"), str):
            item["raw_text"] = item["raw_text"].strip()

        return item

    def close_spider(self):
        spider = self._spider()
        if self._filtered_count and spider:
            spider.logger.info(
                "[岗位过滤] 本次采集共拦截实习/兼职岗位 %d 条", self._filtered_count
            )

    @staticmethod
    def _desensitize(text: str) -> str:
        for pattern, label in CleaningPipeline.PII_PATTERNS:
            text = pattern.sub(f"[{label}]", text)
        return text


# ---------- Item 类型 → ORM 模型映射 ----------
_ITEM_MODEL_MAP = None  # 延迟导入，避免爬虫环境无 app 包时崩溃


def _get_item_model_map():
    """延迟导入 ORM 模型，仅在数据库可用时生效。"""
    global _ITEM_MODEL_MAP
    if _ITEM_MODEL_MAP is None:
        from app.models.raw import CommunityRaw, CourseRaw, JDRaw, PaperRaw
        _ITEM_MODEL_MAP = {
            JobItem: JDRaw,
            CourseItem: CourseRaw,
            PaperItem: PaperRaw,
            CommunityTrendItem: CommunityRaw,
        }
    return _ITEM_MODEL_MAP


class PostgresPipeline:
    """写入 PostgreSQL raw 表（upsert）。

    按 Item 类型路由：
    - JobItem           → jd_raw
    - CourseItem        → course_raw
    - PaperItem         → paper_raw
    - CommunityTrendItem → community_raw

    数据库不可用时降级为仅写 JSONL（不阻塞爬虫）。
    """

    def __init__(self):
        self.crawler = None
        self.engine = None
        self.session_factory = None

    @classmethod
    def from_crawler(cls, crawler):
        """scrapy 2.12+ 移除 pipeline 方法中的 spider 参数，经 crawler 访问 spider。"""
        pipe = cls()
        pipe.crawler = crawler
        return pipe

    def _spider(self):
        return self.crawler.spider if self.crawler is not None else None

    async def open_spider(self):
        spider = self._spider()
        try:
            from app.core.config import settings
            self.engine = create_async_engine(settings.postgres_dsn, echo=False)
            self.session_factory = async_sessionmaker(
                self.engine, class_=AsyncSession, expire_on_commit=False
            )
            if spider:
                spider.logger.info("PostgresPipeline 已连接 PostgreSQL")
        except Exception as e:
            if spider:
                spider.logger.warning(
                    f"PostgresPipeline 初始化失败，降级为仅 JSONL 输出: {e}"
                )
            self.engine = None

    async def close_spider(self):
        spider = self._spider()
        if self.engine:
            await self.engine.dispose()
            if spider:
                spider.logger.info("PostgresPipeline 已关闭数据库连接")

    async def process_item(self, item):
        spider = self._spider()
        if not self.engine:
            return item  # 降级模式

        model_map = _get_item_model_map()
        model = model_map.get(type(item))
        if not model:
            if spider:
                spider.logger.warning(f"未知 Item 类型: {type(item).__name__}，跳过入库")
            return item

        try:
            async with self.session_factory() as session:
                await self._upsert(session, model, item)
                await session.commit()
        except Exception as e:
            if spider:
                spider.logger.error(
                    f"PostgresPipeline 写入 {model.__tablename__} 失败: {e}"
                )

        return item

    @staticmethod
    async def _upsert(session: AsyncSession, model, item):
        """upsert：source + source_id 冲突时更新。"""
        item_dict = dict(item)
        fingerprint = item_dict.pop("_fingerprint", "")
        raw_text = item_dict.pop("raw_text", "")

        stmt = pg_insert(model).values(
            source=item_dict.get("source", ""),
            source_id=item_dict.get("source_id", ""),
            source_url=item_dict.get("source_url", ""),
            crawled_at=item_dict.get("crawled_at", ""),
            fingerprint=fingerprint,
            snapshot=item_dict,
            # 08-14：移除 65535 截断（初始化模板遗留，PG Text 无长度限制；
            # 截断丢失 JD 原文尾部，LLM 抽取输入侧另行裁剪）
            raw_text=str(raw_text) if raw_text else "",
            is_desensitized=item_dict.get("is_desensitized", False),
        )

        constraint_name = f"uq_{model.__tablename__}_source_id"
        stmt = stmt.on_conflict_do_update(
            constraint=constraint_name,
            set_={
                "source_url": stmt.excluded.source_url,
                "crawled_at": stmt.excluded.crawled_at,
                "fingerprint": stmt.excluded.fingerprint,
                # JSONB 合并（右覆盖左同键）：保留已有 extraction/validation/
                # inflation 等下游写入字段，新抓取字段覆盖同名键。此前整体覆盖
                # 会在每次重爬时把已抽取记录"打回"未抽取，导致 ETL 重复 LLM 抽取
                "snapshot": model.__table__.c.snapshot.op("||")(stmt.excluded.snapshot),
                "raw_text": stmt.excluded.raw_text,
                "is_desensitized": stmt.excluded.is_desensitized,
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)
