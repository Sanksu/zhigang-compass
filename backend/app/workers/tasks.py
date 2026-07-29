"""ARQ 异步任务定义。

任务类型：
- resume_parse：简历解析（3-10s）
- batch_extract：LLM 批量抽取（5s+）
- evolution_compute：演化计算（60s+）
"""

from arq import Worker


async def resume_parse(ctx: Worker, file_path: str) -> dict:
    """简历解析异步任务。"""
    return {"status": "pending", "msg": "待实现"}


async def batch_extract(ctx: Worker, jd_ids: list[str]) -> dict:
    """LLM 批量实体抽取异步任务。"""
    return {"status": "pending", "msg": "待实现"}


async def evolution_compute(ctx: Worker, version: str) -> dict:
    """每日演化计算异步任务。"""
    return {"status": "pending", "msg": "待实现"}
