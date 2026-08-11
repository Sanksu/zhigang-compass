"""脚本统一日志工具（scripts/ 全量接入）。

提供带时间戳与级别的日志，并集中抑制第三方库噪音
（SQLAlchemy echo / Neo4j / HF / sentence-transformers），
避免脚本进度日志被库日志淹没。

用法：
    from app.core.logging import setup_logging
    logger = setup_logging("backfill_ingest")   # 返回命名 logger，已配好根格式
    logger.info("开始…")
    logger.error("失败: %s", e)

设计说明：
- 时间戳格式 YYYY-MM-DD HH:MM:SS + 级别 + logger 名，跨脚本可 grep
- force=True 覆盖已有 root handler：脚本独立运行时生效，不影响 app 服务
- 第三方库 logger 统一压到 WARNING（与各脚本历史做法一致）
"""

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# 脚本运行时通常不需要的第三方库日志（历史脚本各自抑制，统一收敛于此）
_QUIET_LOGGERS = (
    "sqlalchemy.engine",
    "sqlalchemy.engine.Engine",
    "neo4j",
    "huggingface_hub",
    "sentence_transformers",
)


def setup_logging(
    name: str = "script",
    level: int = logging.INFO,
    quiet: tuple[str, ...] = (),
    force: bool = True,
    stream=sys.stdout,
) -> logging.Logger:
    """配置根日志格式并返回命名 logger。

    Args:
        name: logger 名（一般传脚本名，便于日志定位）。
        level: 根日志级别，默认 INFO。
        quiet: 额外需要抑制到 WARNING 的第三方 logger 名。
        force: 是否覆盖已有 root 配置，默认 True（独立脚本场景）。
        stream: 日志输出流，默认 stdout（与脚本既有 print 行为一致）。
    """
    logging.basicConfig(
        format=_FORMAT,
        datefmt=_DATEFMT,
        level=level,
        force=force,
        stream=stream,
    )
    for lg_name in (*_QUIET_LOGGERS, *quiet):
        logging.getLogger(lg_name).setLevel(logging.WARNING)
    return logging.getLogger(name)
