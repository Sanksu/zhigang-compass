"""ARQ 异步任务定义。

任务类型：
- resume_parse：简历解析（3-10s）
- batch_extract：LLM 批量抽取（5s+）
- evolution_compute：演化计算（60s+）
"""

from arq import Worker


async def resume_parse(ctx: Worker, file_path: str) -> dict:
    """简历解析异步任务。"""
    try:
        # TODO: 对接 pypdf / python-docx / OCR 管线
        # 1. 读取文件
        # 2. PII 脱敏
        # 3. LLM 抽取结构化信息
        # 4. 写入 resume_cache 表
        return {"status": "success", "msg": "解析完成", "file_path": file_path}
    except Exception as e:
        return {"status": "failed", "msg": str(e), "file_path": file_path}


async def batch_extract(ctx: Worker, jd_ids: list[str]) -> dict:
    """LLM 批量实体抽取异步任务。"""
    try:
        # TODO: 对接 LLM provider + 抽取管线
        # 1. 从 jd_raw 表读取 snapshot
        # 2. 调用 LLM 抽取（技能/岗位/证据）
        # 3. 写入 Neo4j
        total = len(jd_ids)
        return {"status": "success", "msg": f"批量抽取完成，共 {total} 条", "count": total}
    except Exception as e:
        return {"status": "failed", "msg": str(e), "count": 0}


async def evolution_compute(ctx: Worker, version: str) -> dict:
    """每日演化计算异步任务。"""
    try:
        # TODO: 对接演化检测管线
        # 1. 对比当前版本与上一版本快照
        # 2. 计算技能频次变化
        # 3. 标记新兴/衰退技能
        # 4. 写入演化快照
        return {"status": "success", "msg": f"演化计算完成：版本 {version}", "version": version}
    except Exception as e:
        return {"status": "failed", "msg": str(e), "version": version}