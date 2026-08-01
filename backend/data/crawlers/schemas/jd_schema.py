"""JD 结构化输出 Schema（设计文档 §4.3 单一事实源）。

字段清单对齐设计文档：
job_title / company / salary_range / city / experience_required /
education_required / responsibilities / requirements / benefits /
publish_time / source_url / decay_weight

用途：
1. 爬虫输出校验：JobItem → JDSchema 转换时校验字段完整性
2. 入库前校验：PostgresPipeline upsert 前用 Schema 验证
3. 文档对齐：设计文档引用此文件作为字段定义的单一事实源

注意：
- decay_weight 由 M3 时效加权管线计算（design §4.5），爬虫阶段填 1.0
- benefits 当前爬虫未采集（列表页无此字段），留空字符串
"""

from pydantic import BaseModel, Field


class JDSchema(BaseModel):
    """JD 结构化输出 Schema（设计文档 §4.3）。"""

    # ---- 岗位信息 ----
    job_title: str = Field(description="岗位名称")
    company: str = Field(default="", description="公司名称")
    salary_range: str = Field(default="", description="薪资范围文本")
    city: str = Field(default="", description="工作地点（城市）")
    experience_required: str = Field(default="", description="经验要求")
    education_required: str = Field(default="", description="学历要求")

    # ---- 核心内容 ----
    responsibilities: str = Field(default="", description="岗位职责（原始文本）")
    requirements: str = Field(default="", description="任职要求（原始文本）")
    benefits: str = Field(default="", description="福利待遇（原始文本）")

    # ---- 元数据 ----
    publish_time: str = Field(default="", description="发布日期 ISO8601")
    source_url: str = Field(description="原始 URL")
    decay_weight: float = Field(default=1.0, description="时效降权系数，爬虫阶段填 1.0，M3 时效加权管线计算")

    @classmethod
    def from_job_item(cls, item: dict) -> "JDSchema":
        """从 JobItem dict 构造 JDSchema。

        字段映射：
            title → job_title
            location → city
            salary → salary_range
            experience → experience_required
            education → education_required
            description → responsibilities
            requirements → requirements
            post_date → publish_time
        """
        return cls(
            job_title=item.get("title", ""),
            company=item.get("company", ""),
            salary_range=item.get("salary", ""),
            city=item.get("location", ""),
            experience_required=item.get("experience", ""),
            education_required=item.get("education", ""),
            responsibilities=item.get("description", ""),
            requirements=item.get("requirements", ""),
            benefits="",  # 当前爬虫未采集，详情页采集后填充
            publish_time=item.get("post_date", ""),
            source_url=item.get("source_url", ""),
            decay_weight=1.0,  # M3 时效加权管线计算
        )
