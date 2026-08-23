"""路由级冒烟测试（08-15 审查 H2：路由测试缺口闭环起步）。

背景：08-14 装饰器错位回归（@router.get 错挂内部查询函数——参数变必填，
生产 8290 个 422）在 105 个 graph 测试全绿下溜进 develop——既有测试直调
函数绕过路由表，装饰器错位测不出来。本文件做两件事：

1. 静态挂载检查：递归收集全部路由（Starlette 1.3 惰性 _IncludedRouter
   顶层不展开，经 original_router 展开），断言路径唯一、端点不绑定
   内部 helper（以 _ 开头的函数）——装饰器错位类回归的通用捕获器。
2. 端点冒烟：ASGITransport + 全 mock（Neo4j/Redis 依赖打桩），验证
   代表性匿名端点真实挂载且响应结构符合契约（不走外部服务，CI 可跑）。
"""

from typing import Iterator

import httpx
import pytest
from starlette.exceptions import HTTPException

from app.main import app


def _iter_routes(routes, prefix: str = "") -> Iterator[tuple[str, object]]:
    """递归展开路由（处理 _IncludedRouter/Mount/Router 嵌套），拼接 include 前缀。

    Starlette 1.3 的 _IncludedRouter 顶层不展开子路由，且子 APIRoute.path
    不含 include 前缀（/api/v1、/graph 记录在 include_context.prefix）。
    """
    for r in routes:
        ctx = getattr(r, "include_context", None)
        sub_prefix = (ctx.prefix if ctx is not None else "") or ""
        if getattr(r, "original_router", None) is not None:
            yield from _iter_routes(r.original_router.routes, prefix + sub_prefix)
            continue
        nested = getattr(r, "routes", None)
        if nested:
            yield from _iter_routes(nested, prefix + sub_prefix)
            continue
        p = getattr(r, "path", None)
        if p:
            yield prefix + sub_prefix + p, r


def _all_routes() -> list[tuple[str, object]]:
    return list(_iter_routes(app.routes))


class _FakeRedis:
    """内存 Redis 桩：支持图谱缓存和限流的 SET NX EX / INCR 路径。"""

    def __init__(self):
        self.values: dict[str, int | str] = {}
        self.set_calls: list[tuple[str, int | str, bool, int | None]] = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, nx=False, ex=None):
        self.set_calls.append((key, value, nx, ex))
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def incr(self, key):
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = value
        return value


def test_api_routes_collected_and_unique():
    """全量路由可收集、(path, method) 无重复（防 include 重复/路径漂移）。"""
    routes = _all_routes()
    assert routes, "路由收集为空——_IncludedRouter 展开逻辑失效"
    paths = [p for p, _ in routes]
    api_paths = [p for p in paths if p.startswith("/api/v1")]
    assert len(api_paths) > 30, f"API 路由数量异常（{len(api_paths)}），疑似收集不完整"
    seen = []
    for path, r in routes:
        for m in (getattr(r, "methods", None) or {"GET"}):
            seen.append((path, m))
    dup = {x for x in seen if seen.count(x) > 1}
    assert not dup, f"重复路由 (path, method): {dup}"


def test_no_route_bound_to_internal_helper():
    """端点不得绑定内部 helper（以 _ 开头）——panorama 装饰器错位回归的通用捕获。

    08-14 回归形态：@router.get 错挂同步内部函数 _query_panorama，真端点
    失去路由成死代码。此断言覆盖全部模块的同类形态。
    """
    bad = []
    for path, r in _all_routes():
        ep = getattr(r, "endpoint", None)
        name = getattr(ep, "__name__", "")
        if name.startswith("_"):
            bad.append(f"{path} -> {ep.__module__}.{name}")
    assert not bad, f"路由绑定到内部 helper（装饰器错位形态）: {bad}"


@pytest.mark.asyncio
async def test_skill_positions_smoke_mocked(monkeypatch):
    """skill/{id}/positions 冒烟：匿名可见性过滤不破坏响应结构。"""
    from app.api.v1 import graph as graph_mod
    from app.core import middleware as middleware_mod

    async def _mock_skill_positions(skill_id, status_filter):
        return [
            {"position_id": "pos_1", "position_name": "测试岗位",
             "necessity": "must", "weight": 0.8, "level": "中级"},
        ]

    fake_redis = _FakeRedis()
    monkeypatch.setattr(graph_mod, "redis_client", fake_redis)
    monkeypatch.setattr(middleware_mod, "redis_client", fake_redis)
    monkeypatch.setattr(graph_mod, "_query_skill_positions", _mock_skill_positions)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/graph/skill/sk_1/positions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["positions"][0]["position_id"] == "pos_1"
    assert fake_redis.set_calls[0] == ("rate:127.0.0.1:graph", 1, True, 60)


@pytest.mark.asyncio
async def test_graph_view_smoke_mocked(monkeypatch):
    """view/{view_type} 冒烟：视图端点挂载 + 契约结构。"""
    from app.api.v1 import graph as graph_mod
    from app.core import middleware as middleware_mod

    async def _mock_view_main(limit, status_filter):
        return [
            {"p": {"id": "pos_1", "name": "测试岗位", "status": "stable"},
             "s": {"id": "sk_1", "name": "测试技能", "category": "软技能"},
             "r": {"weight": 0.8, "necessity": "must", "level": "中级"}},
        ]

    async def _mock_graph_counts():
        return {"total_nodes": 2, "total_edges": 1}

    fake_redis = _FakeRedis()
    monkeypatch.setattr(graph_mod, "redis_client", fake_redis)
    monkeypatch.setattr(middleware_mod, "redis_client", fake_redis)
    monkeypatch.setattr(graph_mod, "_query_view_main", _mock_view_main)
    monkeypatch.setattr(graph_mod, "_query_graph_counts", _mock_graph_counts)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/graph/view/level")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["view_type"] == "level"
    assert body["data"]["nodes"][0]["id"] == "pos_1"
    # skill_category 透传（软技能粉色渲染数据来源，原 panorama 用例迁移至此）
    skill = next(n for n in body["data"]["nodes"] if n["type"] == "skill")
    assert skill["skill_category"] == "软技能"
    assert fake_redis.set_calls[0] == ("rate:127.0.0.1:graph", 1, True, 60)


def test_hotspot_wrappers_use_async_driver():
    """P2 回归：其余热路径包装（skill_positions/fulltext/view/graph_counts）
    也未回退 to_thread——从数据库模块取 async 驱动直查。"""
    import inspect

    from app.api.v1 import graph as graph_mod

    for name in ("_query_skill_positions", "_query_fulltext_search", "_query_graph_counts",
                 "_query_view_techstack", "_query_view_main"):
        src = inspect.getsource(getattr(graph_mod, name))
        assert src.lstrip().startswith("async def"), f"{name} 应为 async 包装"
        assert "async_neo4j_driver" in src, f"{name} 应使用 async_neo4j_driver"
        assert "asyncio.to_thread" not in src, f"{name} 内不应再 to_thread"


@pytest.mark.asyncio
async def test_spa_fallback_serves_index_for_frontend_routes(tmp_path):
    """前端 history 路由刷新回退 index.html（08-15 修复：/evolution 等 404）。"""
    from app.main import _SPAFallbackStaticFiles

    (tmp_path / "index.html").write_text("<html>智岗罗盘</html>", encoding="utf-8")
    sf = _SPAFallbackStaticFiles(directory=str(tmp_path), html=True)
    scope = {
        "type": "http", "method": "GET", "path": "/evolution",
        "headers": [], "query_string": b"", "scheme": "http",
        "server": ("test", 80), "client": ("127.0.0.1", 1),
        "root_path": "", "app": None, "state": {},
    }
    resp = await sf.get_response("evolution", scope)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
async def test_spa_fallback_preserves_api_404(tmp_path):
    """API 路径 404 保持 JSON（不 fallback 到 index.html，契约响应不受污染）。"""
    from app.main import _SPAFallbackStaticFiles

    (tmp_path / "index.html").write_text("<html>app</html>", encoding="utf-8")
    sf = _SPAFallbackStaticFiles(directory=str(tmp_path), html=True)
    scope = {
        "type": "http", "method": "GET", "path": "/api/v1/nonexistent",
        "headers": [], "query_string": b"", "scheme": "http",
        "server": ("test", 80), "client": ("127.0.0.1", 1),
        "root_path": "", "app": None, "state": {},
    }
    with pytest.raises(HTTPException) as exc:
        await sf.get_response("nonexistent", scope)
    assert exc.value.status_code == 404
