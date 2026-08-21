"""管理后台路由聚合 facade（RBAC admin only）。

各子域路由收敛至 admin_routes/ 子包：accounts（用户）/ audit（审计）/ crawl
（爬虫）/ position_reviews（岗位审核·演化·归档·技术观察）/ position_edit
（岗位人工编辑）/ config（LLM provider + 运行时配置）。本文件仅保留根 router
（prefix=/admin + RBAC 依赖）并按固定顺序 include 子 router——注册顺序即
匹配顺序，/positions/pending 必须先于 /positions/{position_name}；同时
re-export 测试直连的私有符号，保持既有 import 面不变。
"""

from fastapi import APIRouter, Depends

from app.api.deps import require_permission
from app.api.v1.admin_routes import (
    accounts,
    audit,
    config,
    crawl,
    lineage,
    position_edit,
    position_reviews,
)

router = APIRouter(prefix="/admin", dependencies=[Depends(require_permission("admin:*"))])
router.include_router(accounts.router)
router.include_router(audit.router)
router.include_router(crawl.router)
router.include_router(lineage.router)
router.include_router(position_reviews.router)
router.include_router(position_edit.router)
router.include_router(config.router)

# 爬虫域私有符号 re-export（tests/admin/* 直连导入）
PLATFORM_META = crawl.PLATFORM_META
_PLATFORM_TO_SPIDER = crawl._PLATFORM_TO_SPIDER
_history_row = crawl._history_row
_match_platform = crawl._match_platform
_crawl_log_events = crawl._crawl_log_events

# 岗位审核域私有符号 re-export（tests/admin/test_positions_pending、tests/matching 直连导入）
positions_pending = position_reviews.positions_pending
_persist_rejected_change = position_reviews._persist_rejected_change
_persist_position_state = position_reviews._persist_position_state

# 岗位人工编辑域私有符号 re-export（tests/admin/test_position_edit 直连导入）
validate_position_edit = position_edit.validate_position_edit
position_edit_diff = position_edit.position_edit_diff
_get_position_detail_tx = position_edit._get_position_detail_tx
_edit_position_tx = position_edit._edit_position_tx

# 配置域私有符号 re-export（tests/admin/test_llm_config 直连导入）
mask_secret = config.mask_secret
validate_providers = config.validate_providers
save_llm_config = config.save_llm_config
load_llm_config = config.load_llm_config
