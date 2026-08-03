"""Scrapy Pipeline：数据清洗与入库路由。

链路：Item → CleaningPipeline(去重指纹+脱敏+文本标准化)
    → PostgresPipeline(upsert 到 raw 表)
    → [后续] LLM 抽取服务消费 raw 表 → 图谱写入服务 → Neo4j
"""

import hashlib
import re
from datetime import datetime, timezone

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


class CleaningPipeline:
    """基础清洗：去重指纹 + 文本标准化 + 脱敏标记。"""

    # 边界 (?<!\d)/(?!\d) 防止误伤长数字 ID（如 19 位 source_id）中的子串
    PII_PATTERNS = [
        (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "ID_CARD"),  # 18 位身份证优先（避免被 PHONE 子串误匹配）
        (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "PHONE"),
        (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "EMAIL"),
    ]

    def process_item(self, item):
        # 去重指纹：source + source_id 的 SHA256
        item["_fingerprint"] = hashlib.sha256(
            f"{item.get('source', '')}:{item.get('source_id', '')}".encode()
        ).hexdigest()

        # 语义指纹：JobItem 计算 SimHash（title+company+description），跨平台近似去重用
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

        # 脉脉合规脱敏（description 与 raw_text 均含原始文本，需一并清洗）
        if item.get("source") == "maimai":
            item["description"] = self._desensitize(item.get("description", ""))
            item["raw_text"] = self._desensitize(item.get("raw_text", ""))
            item["is_desensitized"] = True

        # 文本标准化：对所有已知文本字段 strip
        for field in _TEXT_FIELDS:
            if item.get(field) and isinstance(item.get(field), str):
                item[field] = item[field].strip()
        if item.get("raw_text") and isinstance(item.get("raw_text"), str):
            item["raw_text"] = item["raw_text"].strip()

        return item

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
            raw_text=str(raw_text)[:65535] if raw_text else "",
            is_desensitized=item_dict.get("is_desensitized", False),
        )

        constraint_name = f"uq_{model.__tablename__}_source_id"
        stmt = stmt.on_conflict_do_update(
            constraint=constraint_name,
            set_={
                "source_url": stmt.excluded.source_url,
                "crawled_at": stmt.excluded.crawled_at,
                "fingerprint": stmt.excluded.fingerprint,
                "snapshot": stmt.excluded.snapshot,
                "raw_text": stmt.excluded.raw_text,
                "is_desensitized": stmt.excluded.is_desensitized,
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)
