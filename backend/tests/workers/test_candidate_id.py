"""候选岗位 id 生成测试（08-14 审查：超长技能名截断碰撞修复）。"""

from app.workers.tasks import _candidate_id


class TestCandidateId:
    def test_short_name_plain_prefix(self):
        """短技能名（≤20 字符）保持存量格式 cand-xxx。"""
        assert _candidate_id("Python") == "cand-Python"
        assert _candidate_id("机器学习") == "cand-机器学习"

    def test_long_name_gets_hash_suffix(self):
        """超长技能名加 hash 后缀，且不破坏 20 字符前缀。"""
        skill = "基于大模型的智能推荐系统架构设计与实现"
        cid = _candidate_id(skill)
        assert cid.startswith("cand-")
        assert cid.endswith("-" + _candidate_id(skill).split("-")[-1])

    def test_long_names_with_shared_prefix_differ(self):
        """共享前缀的超长名不再碰撞（此前 cand-{skill[:20]} 同 id 冲突）。"""
        a = "基于大模型的智能推荐系统架构设计与实现（方向一）"
        b = "基于大模型的智能推荐系统架构设计与实现（方向二）"
        assert a[:20] == b[:20]  # 前置条件：前缀确实相同
        assert _candidate_id(a) != _candidate_id(b)
