"""图谱岗位可见性过滤（方案一：candidate 待审核不外宣）。

graph.py 保留同名兼容绑定（tests/graph/test_graph_visibility.py 直读
graph_api 名下这些名字），本模块为单一事实源。
"""

from typing import Optional

from app.api.deps import role_rank

# 匿名/guest 可见的岗位状态（方案一：candidate 待审核不外宣，archived 已下线）。
# 08-15 语义修正：图谱常态岗位为 active（import_jd/聚合产生），发现候选为
# candidate（persist 镜像）。T-07 开放（08-15 用户决策：T-04 碎片治理两批
# 完成后开放）——active 纳入公开态，匿名可见全部有 JD 支撑岗位。
_PUBLIC_POSITION_STATUSES = ("active", "emerging", "stable", "declining")


def _can_view_all_positions(user: Optional[dict]) -> bool:
    """user/admin 可见全部岗位；匿名/guest 只见 emerging/stable/declining。"""
    return user is not None and role_rank(user) >= role_rank({"role": "user"})


def _position_scope(user: Optional[dict]) -> str:
    """缓存 key 的可见性维度：all=全量（user/admin），public=仅公开态。"""
    return "all" if _can_view_all_positions(user) else "public"


def _status_clause(scope: str) -> str:
    """岗位可见性过滤子句：public 时按公开状态过滤，否则不过滤（Cypher 插值）。"""
    return "p.status IN $public_statuses" if scope == "public" else "true"
