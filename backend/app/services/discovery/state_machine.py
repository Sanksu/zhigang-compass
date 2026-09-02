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
# candidate → emerging：0.6 → 0.55（2026-09-02 审计，P1）。根因：growth 维在
# "观测期首现后即进入平台期"的岗位上天然归零（快照频次末尾平稳，末窗==前窗，
# norm(growth)=0），导致 base = 0.4×norm(ma3) + 0.3×norm(src) 恒卡在 0.55；
# 改 growth 窗口（末窗 vs N 窗）仅能救回有上升脉冲的 CT技师，对 IT/
# AI基础设施工程师（一入池即高位平稳）无效。此类岗位已由存量排除 + 跨源≥2
# 双重防线把关，0.55 是这批"观测期新岗"的真实分布下限，放宽一格可放行。
EMERGING_MIN_CONFIDENCE = 0.55     # candidate → emerging（08-01 初版 0.6，09-02 调至 0.55）
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

# 方案 A（2026-09-02）：图谱 active/存量聚合岗的衰退判定。
# 存量聚合岗无候选池证据锚点，仅凭 jd_publish_windows 补 0 序列会误判——
# 实测 63 个 active 岗里 7 个 dr=1.0，但全是"仅 1 条观测期外旧 JD、末窗补 0"
# 的伪影（可持续发展分析师/GPU验证/保险分析师等，发布日均在 07-14~07-25）。
# 判定需 jd_count 证据量门槛（与 STABLE_MIN_JD_COUNT 同源 5）：只有真实 JD
# 证据量的存量岗才允许判衰退，单条快照补 0 的伪影岗位一律跳过。
DECLINE_MIN_ASSET_JD = 5           # 存量岗判衰退的最小 JD 证据量（防补0伪影）

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
        edges = (snap or {}).get("edges", [])
        # P2 频次口径：仅 REQUIRES 出边计入岗位频次（JD 需求边），
        # HAS_EVIDENCE/BELONGS_TO_OCCUPATION 等维护边不计入，否则频次
        # 被非需求边虚增（快照 edges 自 P2 起导出 relation 字段）。
        # 混布快照口径（第六轮审查算法口径 3，zkt 复核）：快照级 any() 判定
        # 与 evolution（trend_service/_requires_edges）一致——任一边带 relation
        # 标注则无标注边不计入；全部无标注（旧快照）才整期按 REQUIRES 处理，
        # 保持历史窗口序列连续。此前逐边默认 REQUIRES，混布期维护边被计入。
        snapshot_annotated = any(e.get("relation") for e in edges)
        freq: dict[str, int] = {}
        for e in edges:
            rel = e.get("relation")
            if snapshot_annotated and not rel:
                continue
            if rel and rel != "REQUIRES":
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


def evaluate_active_decline(
    windows: WindowFreq,
    jd_count: int,
    source_diversity: int,
) -> Optional[PositionState]:
    """图谱存量聚合岗（active/legacy 无候选池行）的衰退判定（方案 A，2026-09-02）。

    与状态机的 evaluate_auto_transition 不同：存量岗没有候选池证据锚点，
    仅凭 jd_publish_windows 的 30 天窗口序列判定。该序列对"岗位近期无发布"
    会在末尾补 0,而采集期外/数据稀疏的岗位会因此产生伪影 dr=1.0——
    实测 63 个 active 岗里 7 个（可持续发展分析师/GPU验证/保险分析师等，
    jd_cnt=1、发布日 07-14~07-25、观测期 08-02 之前）全部是这种假信号。

    为此加两道存量岗专属门控：
    1. jd_count >= DECLINE_MIN_ASSET_JD（5）：只对真实 JD 证据量的岗位开放,
       单条旧 JD 补 0 的伪影岗跳过——与 stable 的 STABLE_MIN_JD_COUNT 同源,
       保证"证据充分才判衰退"。
    2. 窗口序列 >= 2 期 + 连续 3 窗口累计下降 > 40%：与状态机判定同阈值
       （DECLINE_WINDOW_DROP / DECLINE_WINDOW_COUNT）。

    Args:
        windows: 30 天窗口频次序列（jd_publish_windows 产出）
        jd_count: jd_raw 中该岗位真实 JD 数（防伪影证据量门槛）
        source_diversity: JD 独立源数（源多样性 < 2 的存量岗不判衰退，
            避免单源低证据岗位被误标）

    Returns:
        PositionState.DECLINING 或 None
    """
    if jd_count < DECLINE_MIN_ASSET_JD:
        logger.debug(
            "active_decline 跳过: %s jd_count=%d < %d（证据量不足，防补0伪影）",
            windows, jd_count, DECLINE_MIN_ASSET_JD,
        )
        return None
    if source_diversity < EMERGING_MIN_SOURCES:
        logger.debug(
            "active_decline 跳过: 源多样性=%d < %d（单源低证据不判衰退）",
            source_diversity, EMERGING_MIN_SOURCES,
        )
        return None
    if len(windows.freqs) < 2:
        logger.debug(
            "active_decline 跳过: 窗口序列 %s（<2 期，冷启动不武断判定）",
            windows.freqs,
        )
        return None
    rate = decline_rate(windows, DECLINE_WINDOW_COUNT)
    if rate > DECLINE_WINDOW_DROP:
        logger.debug(
            "active_decline → declining（最近%d窗口下降率 %.3f > %s）",
            DECLINE_WINDOW_COUNT, rate, DECLINE_WINDOW_DROP,
        )
        return PositionState.DECLINING
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
