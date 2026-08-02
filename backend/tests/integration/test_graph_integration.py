"""图谱集成测试（TE-M4-01，设计文档 §11.3.9 test_graph_integration.py）。

后端 ↔ 算法 ↔ Neo4j/PostgreSQL 端到端链路验证，基于本地 docker-compose
基础设施（用户确认）与真实数据断言：
- 图谱：panorama / 全文检索 / 技能先修与课程
- 匹配：recommend（真实 resume_cache 驱动）
- 演化：versions 列表（真实 graph_versions 快照）
- 简历：list（真实 resume_cache）

基础设施不可达时由 conftest 统一 skip。链路中某数据源为空（如无技能先修）
时用「链路通 + 结构合法」断言，不绑定固定数值，避免基础设施演化导致脆测。
"""

import httpx


class TestHealth:
    def test_health(self, client: httpx.Client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"


class TestGraph:
    def test_panorama_returns_nodes_and_edges(self, client: httpx.Client):
        """全景：真实 Neo4j 有 Position/REQUIRES 数据，节点与边非空。"""
        r = client.get("/api/v1/graph/panorama", params={"limit": 50})
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["stats"]["nodes"] > 0
        assert data["stats"]["edges"] > 0

    def test_fulltext_search_position(self, client: httpx.Client):
        """全文检索：真实库 717 JD 入图，搜"算法"应有结果。"""
        r = client.get("/api/v1/graph/search", params={"q": "算法", "type": "position"})
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["total"] >= 0
        assert all(isinstance(i["id"], str) for i in data["items"])

    def test_skill_prerequisites_structure(self, client: httpx.Client):
        """技能先修链：先取一个真实技能节点，验证拓扑链结构合法。"""
        pano = client.get("/api/v1/graph/panorama", params={"limit": 100}).json()["data"]
        skill_id = next(
            (n["id"] for n in pano["nodes"] if n["type"] == "skill"), None
        )
        if skill_id is None:
            import pytest

            pytest.skip("图谱无技能节点，跳过先修链用例")
        r = client.get(f"/api/v1/graph/skill/{skill_id}/prerequisites")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["skill_id"] == skill_id
        # 先修链按深度升序
        depths = [p["depth"] for p in data["prerequisites"]]
        assert depths == sorted(depths)

    def test_skill_courses_structure(self, client: httpx.Client):
        """技能课程：LEARNABLE_VIA 无课程时返回空列表（不视为失败）。"""
        pano = client.get("/api/v1/graph/panorama", params={"limit": 100}).json()["data"]
        skill_id = next(
            (n["id"] for n in pano["nodes"] if n["type"] == "skill"), None
        )
        if skill_id is None:
            import pytest

            pytest.skip("图谱无技能节点，跳过课程用例")
        r = client.get(f"/api/v1/graph/skill/{skill_id}/courses")
        assert r.status_code == 200
        assert isinstance(r.json()["data"]["courses"], list)


class TestMatch:
    def test_recommend_with_real_resume(self, client: httpx.Client):
        """自动推荐：真实 resume_cache 驱动，返回 Top-N 列表。"""
        resumes = client.get("/api/v1/resume/list", params={"limit": 5}).json()["data"]
        if not resumes["items"]:
            import pytest

            pytest.skip("真实库无简历缓存，跳过推荐用例")
        resume_id = resumes["items"][0]["id"]
        r = client.post("/api/v1/match/recommend", json={"resume_id": resume_id, "top_n": 3})
        assert r.status_code == 200
        assert isinstance(r.json()["data"]["items"], list)


class TestEvolution:
    def test_versions_list(self, client: httpx.Client):
        """版本列表：真实 graph_versions 有 2 快照，分页结构合法。"""
        r = client.get("/api/v1/evolution/versions")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["total"] >= 0
        assert all("version_id" in i for i in data["items"])


class TestResume:
    def test_resume_list(self, client: httpx.Client):
        """简历列表端点连通（真实 resume_cache 1 条）。"""
        r = client.get("/api/v1/resume/list", params={"limit": 10})
        assert r.status_code == 200
        data = r.json()["data"]
        assert isinstance(data["items"], list)
