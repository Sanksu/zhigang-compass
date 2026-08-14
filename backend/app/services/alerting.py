"""webhook 告警服务（设计文档 §4.4 异常阈值告警 / §11.1 配置项）。

兼容飞书/钉钉/企业微信群机器人的 POST JSON 协议；未配置
ALERT_WEBHOOK_URL 或 webhook 调用失败时仅记日志，不阻塞主流程
（告警是旁路，不能因告警失败拖垮 ETL/爬虫管线）。
"""

import asyncio
import json
import logging
import urllib.request

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_alert(event: str, message: str, **extra) -> bool:
    """发送 webhook 告警，返回是否成功送达。

    Args:
        event: 事件类型（如 crawl_failed / data_stale）
        message: 人类可读的告警描述
        **extra: 附加结构化字段（源名、日期等，便于机器人卡片透传）
    """
    if not settings.alert_webhook_url:
        logger.warning("[alert] 未配置 ALERT_WEBHOOK_URL，跳过告警 %s: %s", event, message)
        return False
    payload = json.dumps({"event": event, "message": message, **extra}, ensure_ascii=False)
    return await asyncio.to_thread(_post_webhook, payload)


def _post_webhook(payload: str) -> bool:
    """同步发送（放线程池执行，避免阻塞事件循环）。"""
    req = urllib.request.Request(
        settings.alert_webhook_url,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
        logger.info("[alert] 已发送: event=%s len=%d（payload 不打日志防敏感字段泄露）",
                    json.loads(payload).get("event"), len(payload))
        return True
    except Exception as e:  # noqa: BLE001 告警失败不影响主流程
        logger.error("[alert] 发送失败: %s", e)
        return False
