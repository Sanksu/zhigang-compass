"""图谱热路径缓存失效（invalidate_graph_caches）回归测试（08-18 TTL 治理）。"""

import asyncio

from app.api.v1 import graph as graph_api


class _FakeRedis:
    """graph:* 键扫描 + delete 记录。"""

    def __init__(self):
        self.store = {
            "graph:panorama:public:100:0.3:all": "1",
            "graph:search:public:position:Python:1:20": "1",
            "graph:view:techstack:100:public": "1",
            "graph:position:pos_1:public": "1",
            "graph:skill:sk_1:evidence": "1",
            "matching:positions:current": "1",  # 不应被误删（非 graph:* 前缀）
        }
        self.deleted: list[str] = []

    async def scan_iter(self, match: str):
        import fnmatch

        for key in list(self.store):
            if fnmatch.fnmatch(key, match):
                yield key

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
            self.deleted.append(key)
        return len(keys)


def test_invalidate_graph_caches_deletes_all_graph_prefix(monkeypatch):
    """失效覆盖 panorama/view/search/节点详情全部 graph:* 键，不动 matching:*。"""
    fake = _FakeRedis()
    monkeypatch.setattr(graph_api, "redis_client", fake)

    asyncio.run(graph_api.invalidate_graph_caches())

    assert len(fake.deleted) == 5
    assert any("graph:panorama" in k for k in fake.deleted)
    assert any("graph:search" in k for k in fake.deleted)
    assert any("graph:view" in k for k in fake.deleted)
    assert any("graph:position" in k for k in fake.deleted)
    assert any("graph:skill" in k for k in fake.deleted)
    # 岗位匹配共享缓存（matching:*）不受影响
    assert "matching:positions:current" in fake.store


def test_invalidate_graph_caches_no_keys_no_error(monkeypatch):
    """无 graph:* 键时静默通过。"""
    fake = _FakeRedis()
    fake.store = {}
    monkeypatch.setattr(graph_api, "redis_client", fake)

    asyncio.run(graph_api.invalidate_graph_caches())
    assert fake.deleted == []
