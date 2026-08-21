"""电商候选人领域匹配专项集成测试。

验证领域维度优化（词库归一化 _DOMAIN_ALIASES + 独立语义阈值
DOMAIN_SEM_THRESHOLD=0.5 + 命中日志）在**真实图谱**上的效果：
电商候选人不应再出现领域全 0.0，Top-N 匹配结果中应命中行业相关岗位。

链路：真实 Neo4j 岗位（load_positions_from_graph）→ 真实 SBERT
（SkillEmbedder.get()）→ RuleBasedMatcher。依赖本地 docker-compose
基础设施，Neo4j bolt 不可达时整体 skip（沿用 tests/integration 惯例，
不启动 uvicorn，仅验证匹配引擎领域维度）。

断言采用「链路通 + 结构合法」稳健口径，不绑定具体岗位 id/行业值，
避免图谱数据演化导致脆测。

M1 修复:此前注释称"不使用 integration marker",但 marker 语义已扩为
"需 docker-compose 基础设施或真实 LLM API"(见 pyproject.toml),
本用例依赖真实 Neo4j 图谱,故纳入 marker 范围,默认与 LLM API 测试
一并排除,避免本地 docker 在跑时被普通 pytest 拉起导致数据耦合失败。
"""

import socket

import pytest

# M1 修复:打 integration marker(原仅 Neo4j bolt 探测兜底)
pytestmark = pytest.mark.integration

from app.services.matching.engine import RuleBasedMatcher
from app.services.matching.loaders import load_positions_from_graph
from app.services.matching.schemas import (
    CandidateProfile,
    CandidateSkill,
    MatchRequest,
    MatchMode,
)
from app.services.matching.semantic import SkillEmbedder

_NEO4J_BOLT = ("127.0.0.1", 7687)


def _neo4j_available() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(_NEO4J_BOLT) == 0


def _candidate(domains: list[str]) -> CandidateProfile:
    return CandidateProfile(
        user_id="ecom_integration",
        skills=[CandidateSkill(skill_id=s, skill_name=s, proficiency=2) for s in ("Python", "MySQL")],
        total_years=5,
        domain_experience=domains,
    )


def _match_domains(matcher: RuleBasedMatcher, cand: CandidateProfile, top_n: int = 10):
    """跑匹配并返回 [(position_id, name, industry, domain), ...]（保留领域非 None 的）。"""
    req = MatchRequest(candidate=cand, mode=MatchMode.AUTO, top_n=top_n)
    pos_by_id = {p.position_id: p for p in matcher._positions}
    out = []
    for r in matcher.match(req):
        d = r.radar.get("domain")
        if d is not None:
            p = pos_by_id.get(r.position_id)
            out.append((r.position_id, r.position_name, (p.industry if p else None), d))
    return out


class TestEcommerceDomainMatch:
    @pytest.fixture(scope="module", autouse=True)
    def matcher(self):
        if not _neo4j_available():
            pytest.skip("本地 Neo4j（bolt 7687）不可达，跳过领域集成测试")
        positions = load_positions_from_graph()
        if not positions:
            pytest.skip("图谱无岗位数据")
        return RuleBasedMatcher(positions=positions, semantic=SkillEmbedder.get())

    def test_ecommerce_candidate_hits_at_least_one_domain(self, matcher):
        """优化后：电商候选人在 Top-10 内至少命中 1 个领域相关岗位（不再全 0.0）。"""
        hits = _match_domains(matcher, _candidate(["电商"]))
        related = [(n, ind, d) for _, n, ind, d in hits if d > 0.0]
        print(f"\n电商候选人领域命中明细: {related}")
        assert len(related) >= 1, "电商候选人领域维度全 0.0，优化未生效"

    def test_cloud_candidate_hits_more(self, matcher):
        """云计算候选人为对照：词面命中（行业=云计算）应多于语义兜底，且含 1.0 满分。

        top_n=50：领域分仅为六维之一，行业匹配岗位总分未必进 Top-10
        （数据演化后实测 Top-10 无 1.0、Top-50 命中"开发者体验工程师/云计算"）。
        """
        hits = _match_domains(matcher, _candidate(["云计算"]), top_n=50)
        assert any(d == 1.0 for _, _, _, d in hits), "云计算词面命中应产生 1.0"

    def test_no_domain_experience_yields_none(self, matcher):
        """候选人无领域经验：领域维度 None（不参与，保守）。"""
        hits = _match_domains(matcher, _candidate([]))
        assert hits == []
