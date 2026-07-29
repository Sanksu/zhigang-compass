"""岗位状态机（设计文档 7.2.1 节六状态机）。

转换条件：
- → candidate: 自动（规则门控，每日定时任务）
- candidate → emerging: admin 审核 + 置信度 ≥ 0.6 AND source_diversity ≥ 2
- candidate → rejected: admin 审核
- emerging → stable: 自动（置信度 ≥ 0.8 AND 连续 2 窗口波动 < 25%）
- emerging → declining: 自动
- stable → declining: 自动（连续 3 窗口频次下降 > 40%）
- declining → archived: admin 确认衰退
- declining → stable: 自动（频次回升，z_score > 0 连续 2 窗口）
"""

from app.services.discovery.schemas import CandidatePosition, PositionState

# 状态转换合法性表（非法转换抛 ValueError）
VALID_TRANSITIONS: dict[PositionState, set[PositionState]] = {
    PositionState.CANDIDATE: {PositionState.EMERGING, PositionState.REJECTED},
    PositionState.EMERGING: {PositionState.STABLE, PositionState.DECLINING},
    PositionState.STABLE: {PositionState.DECLINING},
    PositionState.DECLINING: {PositionState.ARCHIVED, PositionState.STABLE},
    PositionState.ARCHIVED: set(),   # 终态
    PositionState.REJECTED: set(),   # 终态
}


class PositionStateMachine:
    """岗位状态机接口。

    M3 实现：
    - 自动转换条件判定（emerging→stable / stable→declining / declining→stable）
    - 人工审核转换记录（candidate→emerging/rejected / declining→archived）
    - 状态属性持久化至 Neo4j 节点
    - 审核日志记录
    """

    def transition(
        self,
        candidate: CandidatePosition,
        target_state: PositionState,
        operator: str = "system",
        reason: str = "",
    ) -> CandidatePosition:
        """执行状态转换。

        Args:
            candidate: 候选岗位当前状态
            target_state: 目标状态
            operator: 操作者（system 自动 / admin 用户名）
            reason: 转换原因（人工审核必填）

        Raises:
            ValueError: 非法状态转换
        """
        current = candidate.state
        if target_state not in VALID_TRANSITIONS.get(current, set()):
            raise ValueError(
                f"非法状态转换: {current.value} → {target_state.value}"
            )
        raise NotImplementedError("状态机持久化实现将在 M3 由算法岗完成")
