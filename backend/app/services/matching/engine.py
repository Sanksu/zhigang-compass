"""匹配引擎接口（设计文档 9.4 节）。

M2 阶段仅定义接口契约，核心实现（三维加权 + Sentence-BERT 语义增强）在 M3 由算法岗补充。
四层主权划分：内容加权 → Sentence-BERT 语义扩展 → 规则引擎（经验/学历/证书）→ LLM 软技能推断。
"""

from app.services.matching.schemas import MatchRequest, MatchResult


class MatchEngine:
    """匹配引擎接口。

    M3 实现：
    - Step 1 粗筛：skill→position 倒排索引取 Top-K (K=200)
    - Step 2 精算：三维评分（must/nice/exp）+ CII 通胀修正 + 时效衰减
    - Step 3：按 total DESC 截取 Top-N
    """

    def match(self, request: MatchRequest) -> list[MatchResult]:
        """执行匹配，返回 Top-N 结果列表。

        AUTO 模式：遍历全量岗位返回 Top-N；COMPARE 模式：返回单岗位详细比对。
        """
        raise NotImplementedError("匹配引擎实现将在 M3 由算法岗完成")


class RuleBasedMatcher(MatchEngine):
    """规则基线匹配器（M3 阶段实现）。

    三维加权规则基线，目标 Spearman ≥ 0.7（100 对黄金集）。
    M3 集成 Sentence-BERT 语义增强后目标 ≥ 0.85。
    """

    def match(self, request: MatchRequest) -> list[MatchResult]:
        raise NotImplementedError("规则基线匹配将在 M3 实现")
