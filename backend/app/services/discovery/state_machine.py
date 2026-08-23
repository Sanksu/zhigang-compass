"""岗位六状态机（设计文档 7.2.1 节）。

转换条件（窗口为 30 天滑动窗口）：
- → candidate: 自动（规则门控，每日定时任务，detector.passes_gate）
- candidate → emerging: admin 审核 + 置信度 ≥ 0.6 AND source_diversity ≥ 2（§7.2.4 阈值表）
- candidate → rejected: admin 审核
- emerging → stable: 自动（jd_count ≥ 5 AND 连续 2 窗口波动 < 25% AND source_diversity ≥ 2，§7.2.1）
- emerging → declining: 自动（连续 3 窗口频次下降 > 40%）
- stable → declining: 自动（连续 3 窗口频次下降 > 40%）
- declining → archived: admin 确认衰退
- declining → stable: 自动（z_score > 0 连续 2 窗口回升）

实现分层：
- 纯函数判定（evaluate_auto_transition / can_promote_to_emerging）：可单测
- PositionStateMachine.transition：校验合法性 + 幂等持久化 Neo4j（Position.status）
   + 人工审核写 AuditLog（operator=admin 用户名，reason 必填）
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.services.discovery.schemas import CandidatePosition, PositionState

logger = logging.getLogger(__name__)

# 状态转换合法性表（非法转换抛 ValueError）
VALID_TRANSITIONS: dict[PositionState, set[PositionState]] = {
    PositionState.CANDIDATE: {PositionState.EMERGING, PositionState.REJECTED},
    PositionState.EMERGING: {PositionState.STABLE, PositionState.DECLINING},
    PositionState.STABLE: {PositionState.DECLINING},
    PositionState.DECLINING: {PositionState.ARCHIVED, PositionState.STABLE},
    PositionState.ARCHIVED: set(),   # 终态
    PositionState.REJECTED: set(),   # 终态
}

# 转换阈值（设计文档 7.2.4 阈值表 + 7.2.1 状态机表）
EMERGING_MIN_CONFIDENCE = 0.6      # candidate → emerging
EMERGING_MIN_SOURCES = 2
STABLE_MIN_JD_COUNT = 5            # emerging → stable：jd_count ≥ 5（§7.2.1 表格）
STABLE_MAX_WINDOW_VOLATILITY = 0.25  # 连续 2 窗口波动 < 25%
STABLE_MAX_SKILL_NOVELTY = 0.2     # skill_novelty 阈值（08-15 需求调整：文档 0.3
                                    # → 0.2——自适应参考周期下 0.3 仅需技能出现
                                    # ≥0.7×生命周期即成熟，冷启动 33 天图谱下
                                    # 23 天即成熟偏宽松；0.2 需 ≥0.8×生命周期）
DECLINE_WINDOW_DROP = 0.40         # 连续 3 窗口频次下降 > 40%
DECLINE_WINDOW_COUNT = 3
RECOVERY_WINDOW_COUNT = 2          # z_score > 0 连续 2 窗口回升

_TZ_CN = timezone(timedelta(hours=8))


@dataclass
class WindowFreq:
    """岗位频次窗口数据（判定输入）。

    freqs: 最近窗口频次（index 0 为最早窗口，按快照时间升序）
    z_scores: 对应窗口 Z-score（与 freqs 等长同顺序，仅 recovery 判定用，可为空）
    """
    freqs: list[float]
    z_scores: list[float] = field(default_factory=list)


def position_freq_windows(
    snapshots: list[dict],
    position_names: set[str],
) -> dict[str, list[float]]:
    """从图谱版本快照序列重建岗位频次窗口序列（时间升序）。

    每期快照：岗位节点作为边的源节点的计数即当期频次；同名归一化岗位
    归并为同一序列。返回 {岗位名: [各期频次]}（按快照时间升序）。

    Args:
        snapshots: graph_versions.snapshot_json 列表（[{nodes, edges}]，时间升序）
        position_names: 关注的岗位名集合（快照 nodes 中 name 匹配）

    Returns:
        岗位名 → 频次序列（与快照窗口数等长，某期无关联边补 0）
    """
    name_by_id: dict[str, str] = {}
    for snap in snapshots:
        for n in (snap or {}).get("nodes", []):
            if n.get("type") == "position" and n.get("name") in position_names:
                name_by_id[n.get("id", "")] = n.get("name", "")

    n_windows = len(snapshots)
    # 每岗位 id 预置全 0 序列：岗位在某期快照无关联边时补 0，
    # 保证不同岗位序列等长对齐（否则跨岗位比较窗口错位）
    freq_by_id: dict[str, list[int]] = {
        pid: [0] * n_windows for pid in name_by_id
    }
    for wi, snap in enumerate(snapshots):
        freq: dict[str, int] = {}
        for e in (snap or {}).get("edges", []):
            # P2 频次口径：仅 REQUIRES 出边计入岗位频次（JD 需求边），
            # HAS_EVIDENCE/BELONGS_TO_OCCUPATION 等维护边不计入，否则频次
            # 被非需求边虚增（快照 edges 自 P2 起导出 relation 字段）。
            # 缺 relation 的旧快照按 REQUIRES 处理，保持历史窗口序列连续。
            if e.get("relation", "REQUIRES") != "REQUIRES":
                continue
            src = e.get("source", "")
            if src in name_by_id:
                freq[src] = freq.get(src, 0) + 1
        for pos_id, count in freq.items():
            freq_by_id[pos_id][wi] = count

    # 同名岗位可能对应多个 pos_id（归一化合并）：按窗口逐项求和，
    # 即该岗位名当期被引用的总边数（与聚合口径一致）
    merged: dict[str, list[float]] = {}
    for pos_id, name in name_by_id.items():
        seq = freq_by_id[pos_id]
        if name not in merged:
            merged[name] = [float(c) for c in seq]
            continue
        merged[name] = [
            merged[name][i] + seq[i] for i in range(n_windows)
        ]
    return merged


def jd_publish_windows(
    daily_freqs: dict[str, dict[str, int]],
    window_days: int = 30,
) -> dict[str, list[float]]:
    """按 JD 发布日聚合岗位频次窗口序列（时间升序）。

    信号源说明（2026-08-11）：declining 判定信号源从"图谱快照 REQUIRES 边数"
    改为"jd_raw 按 post_date 的真实 JD 发布数"——快照边数随图谱清理/重建/改名
    剧烈波动（08-11 重建致"算法工程师"1348→56 伪降），而发布数语义 = 设计文档
    "JD 需求下降"（decline_rate = (prev - curr)/prev，30 天窗口）。

    Args:
        daily_freqs: {岗位名: {ISO 日期: 当日 JD 发布数}}（post_date 缺失按入库日兜底）
        window_days: 窗口天数（默认 30，设计文档 7.2.1）

    Returns:
        {岗位名: [各窗口频次]}（时间升序，以全部日期最晚日为终点对齐；
        岗位窗口未覆盖处补 0——某岗位近期无发布会在末尾出现 0，即下降信号）
    """
    all_days = [d for days in daily_freqs.values() for d in days]
    if not all_days:
        return {}
    end = max(date.fromisoformat(d) for d in all_days)
    out: dict[str, list[float]] = {}
    for name, day_counts in daily_freqs.items():
        buckets: dict[int, int] = {}
        for d, count in day_counts.items():
            idx = (end - date.fromisoformat(d)).days // window_days
            buckets[idx] = buckets.get(idx, 0) + count
        out[name] = [
            float(buckets.get(i, 0)) for i in range(max(buckets), -1, -1)
        ]
    return out


def window_volatility(w: WindowFreq, n: int = 2) -> float:
    """最近 n 窗口末窗相对前一窗口的"萎缩幅度"（不对称，0~1）。

    只把需求萎缩视为波动：last 相对 prev 的下降比例 (prev-last)/prev；
    增长（last > prev，如新源首采接入产生 JD 爆发）不构成波动——避免
    观测冷启动期被误判为不稳定（08-19 诊断：25 个 emerging 全因对称
    (max-min)/max 逼近 100% 而无法晋级 stable）。显著萎缩由 decline_rate
    单独判为 declining。prev 为 0（前窗无数据）无萎缩可谈，取 0。

    Note: n 参数保留以兼容既有调用（判定固定取最近 2 窗口）。
    """
    if len(w.freqs) < 2:
        return 0.0
    prev, last = w.freqs[-2], w.freqs[-1]
    if prev <= 0:
        return 0.0
    return max(0.0, (prev - last) / prev)


def decline_rate(w: WindowFreq, n: int = 3) -> float:
    """最近 n 个窗口的累计下降率（(首-末)/首，首频次为 0 时取 0）。"""
    recent = w.freqs[-n:]
    if len(recent) < 2 or recent[0] == 0:
        return 0.0
    return (recent[0] - recent[-1]) / recent[0]


def freq_z_scores(freqs: list[float]) -> list[float]:
    """频次序列的逐窗口 Z-score（与 freqs 等长、同顺序）。

    以序列自身均值/标准差为基准：z > 0 表示当期频次高于历史均值，
    用于 declining → stable 的回迁判定（连续 2 窗口回升）。

    Returns:
        等长 z-score 序列；序列标准差为 0（全部相等）时各窗口 z 取 0（无信号）
    """
    if not freqs:
        return []
    mean = sum(freqs) / len(freqs)
    variance = sum((f - mean) ** 2 for f in freqs) / len(freqs)
    std = variance ** 0.5
    if std == 0:
        return [0.0] * len(freqs)
    return [(f - mean) / std for f in freqs]


def has_recovery(w: WindowFreq, n: int = RECOVERY_WINDOW_COUNT) -> bool:
    """最近连续 n 个窗口 z_score > 0（频次回升，declining → stable 回迁）。"""
    return len(w.z_scores) >= n and all(z > 0 for z in w.z_scores[-n:])


def can_promote_to_emerging(
    candidate: CandidatePosition,
    confidence: Optional[float] = None,
) -> bool:
    """candidate → emerging 判定（admin 审核通过前的条件校验，§7.2.4）。

    Args:
        candidate: 候选岗位
        confidence: 最终置信度（缺省取 candidate.confidence.final_confidence）
    """
    if confidence is None:
        confidence = (
            candidate.confidence.final_confidence if candidate.confidence else 0.0
        )
    return (
        confidence >= EMERGING_MIN_CONFIDENCE
        and candidate.features.source_diversity >= EMERGING_MIN_SOURCES
    )


def evaluate_auto_transition(
    candidate: CandidatePosition,
    windows: WindowFreq,
    jd_count: Optional[int] = None,
    skill_novelty: Optional[float] = None,
) -> Optional[PositionState]:
    """自动转换判定（emerging/stable/declining 三态自动流转）。

    Args:
        candidate: 候选岗位
        windows: 频次窗口序列
        jd_count: 岗位真实 JD 数（任务层从 jd_raw 统计传入，§7.2.1 门槛）。
            None 时回退 len(candidate.evidence_refs)——注意发现链路
            evidence_refs 多为 watch 标记非真实证据，任务层必须传真实值
        skill_novelty: 岗位技能新颖度 [0,1]（任务层从 Skill.first_seen
            计算传入，§7.2.1 门槛 < 0.2）。None = 数据不可得，不拦截
            （岗位无技能/图谱不可达等，保持现有行为）

    Returns:
        建议的目标状态；无需迁移返回 None
    """
    state = candidate.state
    logger.debug(
        "auto_transition 判定: position=%s state=%s "
        "windows=%s z_scores=%s volatility=%.3f decline_rate=%.3f",
        candidate.position_name, state.value,
        windows.freqs, windows.z_scores,
        window_volatility(windows), decline_rate(windows, DECLINE_WINDOW_COUNT),
    )
    if state == PositionState.EMERGING:
        # §7.2.1 表格：stable 进入条件 = jd_count ≥ 5 + 跨 ≥2 源 + 连续 2 窗口
        # 波动 < 25% + skill_novelty < 0.2（08-15 全量对齐：此前用
        # confidence ≥ 0.8 替代 jd_count 门槛——jd_count=3 时其他维度满分
        # 也能过 0.8，小基数岗位提前稳定）。
        # 波动口径（08-19 修正）：window_volatility 只惩罚末窗相对前窗的萎缩
        # （(prev-last)/prev），首采接入带来的增长 JD 爆发不再误判为不稳定——
        # 诊断显示 25 个 emerging 全因旧对称 (max-min)/max≈100% 无法晋级。
        # jd_count 由任务层从 jd_raw 统计传入；skill_novelty 由任务层从
        # Skill.first_seen 平均图谱年龄归一化传入（None 不拦截）。
        jd = jd_count if jd_count is not None else len(candidate.evidence_refs)
        if (
            jd >= STABLE_MIN_JD_COUNT
            and window_volatility(windows) < STABLE_MAX_WINDOW_VOLATILITY
            and candidate.features.source_diversity >= EMERGING_MIN_SOURCES
            and (skill_novelty is None or skill_novelty < STABLE_MAX_SKILL_NOVELTY)
        ):
            logger.debug(
                "  → stable（jd_count=%d ≥ %d/波动/源多样性/novelty=%s 均达标）",
                jd, STABLE_MIN_JD_COUNT,
                f"{skill_novelty:.3f}" if skill_novelty is not None else "N/A",
            )
            return PositionState.STABLE
        if decline_rate(windows, DECLINE_WINDOW_COUNT) > DECLINE_WINDOW_DROP:
            logger.debug("  → declining（最近3窗口下降率 > %s）", DECLINE_WINDOW_DROP)
            return PositionState.DECLINING
    elif state == PositionState.STABLE:
        if decline_rate(windows, DECLINE_WINDOW_COUNT) > DECLINE_WINDOW_DROP:
            logger.debug("  → declining（最近3窗口下降率 > %s）", DECLINE_WINDOW_DROP)
            return PositionState.DECLINING
    elif state == PositionState.DECLINING:
        if has_recovery(windows):
            logger.debug("  → stable（最近%d窗口 z>0 回升）", RECOVERY_WINDOW_COUNT)
            return PositionState.STABLE
    logger.debug("  → 不迁移")
    return None


class PositionStateMachine:
    """岗位状态机：校验合法性 + 幂等持久化 Neo4j + 人工审核审计日志。"""

    def transition(
        self,
        candidate: CandidatePosition,
        target_state: PositionState,
        operator: str = "system",
        reason: str = "",
    ) -> CandidatePosition:
        """执行状态转换（校验合法性，不依赖外部存储）。

        Args:
            candidate: 候选岗位当前状态
            target_state: 目标状态
            operator: 操作者（system 自动 / admin 用户名）
            reason: 转换原因（人工审核必填）

        Raises:
            ValueError: 非法状态转换，或人工审核缺少 reason

        Returns:
            转换后的 CandidatePosition 副本
        """
        current = candidate.state
        if target_state not in VALID_TRANSITIONS.get(current, set()):
            raise ValueError(
                f"非法状态转换: {current.value} → {target_state.value}"
            )
        if operator != "system" and not reason.strip():
            raise ValueError("人工审核状态转换必须填写 reason")
        return candidate.model_copy(update={"state": target_state})

    def persist(
        self,
        neo4j_session,
        candidate: CandidatePosition,
        target_state: PositionState,
        *,
        db=None,
        operator: str = "system",
        reason: str = "",
    ) -> CandidatePosition:
        """持久化状态：Neo4j Position.status 幂等更新 + 人工审核写 AuditLog。

        幂等设计：按 name MERGE Position 节点并 SET status，重复执行结果一致。

        Args:
            neo4j_session: Neo4j 会话（execute_write 语义）
            candidate: 候选岗位
            target_state: 目标状态（先校验合法性再落库）
            db: PostgreSQL 会话（可选，仅 operator != system 时写审计日志）
            operator/reason: 同 transition

        Returns:
            转换后的 CandidatePosition
        """
        updated = self.transition(candidate, target_state, operator, reason)
        state = updated.state.value

        def _persist_tx(tx) -> None:
            # 补全字段（08-14 修复）：候选晋升时图谱可能尚无该岗位节点（JD 尚未
            # 聚合入图），MERGE 会创建无 id/freq 的残缺节点，下游 loaders 以 p.id
            # 为主键得 None；创建时补 id（与 import_jd 同源 next_id）与 freq=0
            from app.services.kg.id_generator import next_id

            pid = next_id(tx, "Position")
            tx.run(
                """
                MERGE (p:Position {name: $name})
                ON CREATE SET p.id = $pid, p.freq = 0
                SET p.status = $state, p.state_updated_at = $now
                """,
                name=updated.position_name,
                pid=pid,
                state=state,
                now=datetime.now(_TZ_CN).isoformat(timespec="seconds"),
            )

        neo4j_session.execute_write(_persist_tx)

        if operator != "system" and db is not None:
            from app.models.business import AuditLog

            db.add(
                AuditLog(
                    user_id=operator,
                    action="discovery.state_transition",
                    resource="Position",
                    resource_id=updated.position_name,
                    detail={
                        "from_state": candidate.state.value,
                        "to_state": state,
                        "reason": reason,
                        "seed_matched": updated.seed_matched,
                        "rag_matched": updated.rag_matched,
                    },
                )
            )
        return updated
