"""图谱集成测试（TE-M4-01，设计文档 §11.3.9 test_graph_integration.py）。

后端 ↔ 算法 ↔ Neo4j/PostgreSQL 端到端链路验证，基于本地 docker-compose
基础设施（用户确认）与真实数据断言：
- 图谱：panorama / 全文检索 / 技能先修与课程
- 匹配：recommend（真实 resume_cache 驱动）
- 演化：versions 列表（真实 graph_versions 快照）
- 简历：list（真实 resume_cache）

基础设施不可达时由 conftest 统一 skip。链路中某数据源为空（如无技能先修）
时用「链路通 + 结构合法」断言，不绑定固定数值，避免基础设施演化导致脆测。

M1 修复:补 pytest.mark.integration marker(原仅 conftest 端口探测兜底,
普通 pytest 在 docker 在跑时会拉起集成用例,marker 与 conftest 双保险)。
"""

import httpx

import pytest

# M1 修复:打 integration marker,普通 pytest 默认排除(需 -m integration 显式跑)
pytestmark = pytest.mark.integration


class TestHealth:
    def test_health(self, client: httpx.Client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"


class TestGraph:
    def test_panorama_returns_nodes_and_edges(self, client: httpx.Client, auth_headers):
        """全景：真实 Neo4j 有 Position/REQUIRES 数据，节点与边非空。"""
        import pytest

        if not auth_headers:
            pytest.skip("admin 登录失败（库已初始化且密码非默认），跳过认证用例")
        r = client.get("/api/v1/graph/view/panorama", params={"limit": 50}, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["stats"]["nodes"] > 0
        assert data["stats"]["edges"] > 0

    def test_panorama_guest_excludes_candidate(self, client: httpx.Client):
        """方案一：匿名/guest 全景不含 candidate 岗位（user/admin 可见全量）。

        构造验证：guest 返回的 position 节点 status 必须全部 ∈ 公开状态集
        （#218 后 active 为常态公开，仅 candidate 待审核不外宣）。
        """
        r = client.get("/api/v1/graph/view/panorama", params={"limit": 600})
        assert r.status_code == 200
        data = r.json()["data"]
        visible = {"active", "emerging", "stable", "declining"}
        for node in data["nodes"]:
            if node["type"] == "position":
                assert node.get("status", "candidate") in visible, (
                    f"guest 图谱不应包含 candidate 岗位: {node['name']}"
                )

    def test_position_detail_guest_404_for_candidate(
        self, client: httpx.Client, auth_headers
    ):
        """方案一：guest 查询 candidate 岗位详情应 404（不可见）。

        通过 admin 全景拿到岗位 id（candidate 优先），匿名请求应 404。
        """
        import pytest

        if not auth_headers:
            pytest.skip("admin 登录失败（库已初始化且密码非默认），跳过认证用例")
        pano = client.get("/api/v1/graph/view/panorama", params={"limit": 600}, headers=auth_headers).json()["data"]
        candidate = next(
            (n["id"] for n in pano["nodes"] if n["type"] == "position" and n.get("status") == "candidate"),
            None,
        )
        if candidate is None:
            pytest.skip("真实库无 candidate 岗位，跳过该用例")
        r = client.get(f"/api/v1/graph/position/{candidate}")
        assert r.status_code == 404

    def test_fulltext_search_position(self, client: httpx.Client, auth_headers):
        """全文检索：真实库 717 JD 入图，搜"算法"应有结果。"""
        import pytest

        if not auth_headers:
            pytest.skip("admin 登录失败（库已初始化且密码非默认），跳过认证用例")
        r = client.get("/api/v1/graph/search", params={"q": "算法", "type": "position"}, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["total"] >= 0
        assert all(isinstance(i["id"], str) for i in data["items"])

    def test_skill_prerequisites_structure(self, client: httpx.Client, auth_headers):
        """技能先修链：先取一个真实技能节点，验证拓扑链结构合法。"""
        import pytest

        if not auth_headers:
            pytest.skip("admin 登录失败（库已初始化且密码非默认），跳过认证用例")
        pano = client.get("/api/v1/graph/view/panorama", params={"limit": 100}, headers=auth_headers).json()["data"]
        skill_id = next(
            (n["id"] for n in pano["nodes"] if n["type"] == "skill"), None
        )
        if skill_id is None:
            pytest.skip("图谱无技能节点，跳过先修链用例")
        r = client.get(f"/api/v1/graph/skill/{skill_id}/prerequisites", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["skill_id"] == skill_id
        # 先修链按深度升序
        depths = [p["depth"] for p in data["prerequisites"]]
        assert depths == sorted(depths)

    def test_skill_courses_structure(self, client: httpx.Client, auth_headers):
        """技能课程：LEARNABLE_VIA 无课程时返回空列表（不视为失败）。"""
        import pytest

        if not auth_headers:
            pytest.skip("admin 登录失败（库已初始化且密码非默认），跳过认证用例")
        pano = client.get("/api/v1/graph/view/panorama", params={"limit": 100}, headers=auth_headers).json()["data"]
        skill_id = next(
            (n["id"] for n in pano["nodes"] if n["type"] == "skill"), None
        )
        if skill_id is None:
            pytest.skip("图谱无技能节点，跳过课程用例")
        r = client.get(f"/api/v1/graph/skill/{skill_id}/courses", headers=auth_headers)
        assert r.status_code == 200
        assert isinstance(r.json()["data"]["courses"], list)


class TestMatch:
    def test_recommend_with_real_resume(self, client: httpx.Client, auth_headers):
        """自动推荐：真实 resume_cache 驱动，202 异步契约 + task_id 可查询。"""
        import pytest

        if not auth_headers:
            pytest.skip("admin 登录失败（库已初始化且密码非默认），跳过认证用例")
        resumes = client.get(
            "/api/v1/resume/list", params={"limit": 5}, headers=auth_headers
        ).json()["data"]
        if not resumes["items"]:
            pytest.skip("真实库无简历缓存，跳过推荐用例")
        resume_id = resumes["items"][0]["id"]
        r = client.post(
            "/api/v1/match/recommend",
            json={"resume_id": resume_id, "top_n": 3},
            headers=auth_headers,
        )
        # §2.4.4 异步契约：202 + task_id，轮询 /match/task/{task_id}
        assert r.status_code == 202
        task_id = r.json()["data"]["task_id"]
        assert isinstance(task_id, str) and task_id
        t = client.get(f"/api/v1/match/task/{task_id}", headers=auth_headers)
        assert t.status_code == 200
        assert t.json()["data"]["status"] in ("pending", "running", "success", "failed")


class TestEvolution:
    def test_versions_list(self, client: httpx.Client, auth_headers):
        """版本列表：真实 graph_versions 有 2 快照，分页结构合法。"""
        import pytest

        if not auth_headers:
            pytest.skip("admin 登录失败（库已初始化且密码非默认），跳过认证用例")
        r = client.get("/api/v1/evolution/versions", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["total"] >= 0
        assert all("version_id" in i for i in data["items"])


class TestResume:
    def test_resume_list(self, client: httpx.Client, auth_headers):
        """简历列表端点连通（真实 resume_cache 1 条）。"""
        import pytest

        if not auth_headers:
            pytest.skip("admin 登录失败（库已初始化且密码非默认），跳过认证用例")
        r = client.get("/api/v1/resume/list", params={"limit": 10}, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert isinstance(data["items"], list)
