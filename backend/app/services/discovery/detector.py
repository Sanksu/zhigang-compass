"""新岗位发现检测器（设计文档 7.2.3 节判定流程）。

单阶段判定：Z-score 统计门控 + RAG 接地。
阶段一（Z-score 门控，仅 JD 源驱动）：
  IF z_score > 2.0 AND source_diversity ≥ 2 AND jd_freq_ma3 ≥ 10 → candidate
  ELIF z_score > 1.5 AND source_diversity ≥ 2 → candidate（保守观察）
  ELSE → 继续监测
冷启动（历史 < 60 天）：wilson_lower > 0.3 AND source_diversity ≥ 3 → candidate
阶段二（RAG 接地 + 种子列表，M4 开放自动 emerging 判定，M3 影子模式人工触发）
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Protocol
from uuid import uuid4

from app.services.discovery.schemas import (
    CandidatePosition,
    DiscoveryFeatures,
    PositionState,
)
from app.services.discovery.confidence import (
    wilson_lower,
    WILSON_COLD_START_THRESHOLD,
)

# Z-score 门控阈值
Z_SCORE_STRICT = 2.0       # 严格门控
Z_SCORE_CONSERVATIVE = 1.5  # 保守观察
MIN_SOURCE_DIVERSITY = 2
MIN_JD_FREQ_MA3 = 10
# 冷启动源多样性（2026-08-11 调降 3→2：低频新岗位通常仅 1-2 个源先出现，
# 3 源门槛会永久拦截"单源或双源新出现"的岗位，与成熟岗位排除机制配合后安全）
MIN_SOURCE_COLD_START = 2

# 冷启动窗口阈值（设计文档 7.2.3 节）
COLD_START_DAYS = 60

# 项目统一时区 UTC+8
_TZ_CN = timezone(timedelta(hours=8))


@dataclass
class DiscoveryInput:
    """单个技能的发现判定输入。

    cold_successes/cold_total 仅冷启动（z_score 不可算）时使用，为 Wilson score
    兜底提供二项分布样本；正常窗口无需提供。

    first_seen_date/observation_start 用于成熟岗位排除：岗位首次观测日期早于
    观测窗口起点即视为存量成熟岗位，不参与发现判定（防候选池被采集存量霸占）。
    """

    position_name: str
    features: DiscoveryFeatures
    history_days: int
    cold_successes: Optional[int] = None
    cold_total: Optional[int] = None
    first_seen_date: Optional[str] = None
    observation_start: Optional[str] = None


class CandidateProvider(Protocol):
    """候选输入数据源（M3 由 PostgreSQL 聚合层实现）。"""

    def iter_inputs(self) -> Iterable[DiscoveryInput]:
        """遍历全部待判定技能。"""
        ...


def passes_gate(features: DiscoveryFeatures, history_days: int) -> bool:
    """判定特征是否通过 candidate 门控。

    Z-score 门控为主判定（设计文档 §7.2.3）：z_score 可计算（≥2 个快照窗口，
    §7.2.1 时间窗口演化的 Z-score 基线）即使用 Z-score 门控，与历史窗口天数
    无关。修复：此前 history_days < COLD_START_DAYS 一律返回 False，导致
    快照跨度 <60 天时即使 z_score 已算出的岗位也被强制走冷启动 Wilson，而
    Wilson 下界在 JD 占比下极低（实测 0.005-0.185）永不达 0.3 阈值，
    candidate 发现功能整体失效。

    z_score 为 None（快照窗口不足，无法计算 Z-score）时才走冷启动
    Wilson 兜底（passes_cold_start_gate，由上层 DiscoveryInput 提供二项样本）。

    Args:
        features: 4 核心特征
        history_days: 历史窗口天数（仅 z_score 为 None 时用于判断是否走
            冷启动；本函数内部不再以天数禁用 Z-score 门控）

    Returns:
        True 表示进入 candidate 池
    """
    if features.z_score is None:
        return False

    # Z-score 门控（主判定，与历史天数无关）
    if features.z_score > Z_SCORE_STRICT:
        return (
            features.source_diversity >= MIN_SOURCE_DIVERSITY
            and features.jd_freq_ma3 >= MIN_JD_FREQ_MA3
        )
    if features.z_score > Z_SCORE_CONSERVATIVE:
        return features.source_diversity >= MIN_SOURCE_DIVERSITY
    return False


def passes_cold_start_gate(
    successes: int,
    total: int,
    source_diversity: int,
) -> bool:
    """冷启动 Wilson score 兜底门控。"""
    if source_diversity < MIN_SOURCE_COLD_START:
        return False
    return wilson_lower(successes, total) > WILSON_COLD_START_THRESHOLD


def _is_mature_position(inp: DiscoveryInput) -> bool:
    """存量成熟岗位排除：岗位首次观测日早于观测窗口起点即为存量。

    仅当两项日期信息都齐备才判定（缺任一视为非成熟，不误伤）；ISO 字符串
    直接字典序比较（YYYY-MM-DD 长度固定可比较）。
    """
    if not inp.first_seen_date or not inp.observation_start:
        return False
    return inp.first_seen_date < inp.observation_start


class DiscoveryDetector:
    """新岗位发现检测器。

    阶段一已实现（Z-score/Wilson 门控 + candidate 组装）；
    阶段二 RAG 接地为 M4 交付项，M3 影子模式仅人工触发（ground_with_rag 保持 stub）。
    """

    def detect_candidates(self, provider: CandidateProvider) -> list[CandidatePosition]:
        """扫描全量技能，输出新进入 candidate 池的岗位列表。

        Args:
            provider: 特征数据源，逐技能提供门控所需输入

        Returns:
            通过阶段一门控的 CandidatePosition 列表（state=candidate）
        """
        candidates: list[CandidatePosition] = []
        for inp in provider.iter_inputs():
            if self._passes(inp):
                candidates.append(self._build_candidate(inp))
        return candidates

    def _passes(self, inp: DiscoveryInput) -> bool:
        """阶段一门控：先排除存量成熟岗位，再 Z-score 门控为主，z_score 不可算时冷启动兜底。

        成熟岗位排除（2026-08-11）：岗位首次观测日期（first_seen_date）早于观测
        窗口起点（observation_start）即视为存量成熟岗位——系统开始观测前就已
        存在的市场存量，是采集起步期候选池被"算法/后端/全栈"等热门岗位霸占的
        根因（量化：27 个候选 25 个采集首日即在）。此类岗位即使近期 JD 增长也
        不是"新出现的岗位"，两条门控路径（Z-score/Wilson）一律拦截。

        Z-score 门控（z_score 非 None）优先（§7.2.3 主判定）；仅当 z_score 为
        None（快照窗口不足无法计算）才走冷启动 Wilson（需二项样本 successes/total）。
        冷启动不再以 history_days 天数判定——天数 <60 但 z_score 已由多期快照
        算出时，应直接使用 Z-score（修复：此前 history_days<60 强制禁用 Z-score
        导致 candidate 发现失效）。
        """
        if _is_mature_position(inp):
            return False
        if passes_gate(inp.features, inp.history_days):
            return True
        if (
            inp.features.z_score is None
            and inp.cold_successes is not None
            and inp.cold_total is not None
        ):
            return passes_cold_start_gate(
                inp.cold_successes, inp.cold_total, inp.features.source_diversity
            )
        return False

    @staticmethod
    def _build_candidate(inp: DiscoveryInput) -> CandidatePosition:
        return CandidatePosition(
            candidate_id=f"cand-{uuid4().hex[:12]}",
            position_name=inp.position_name,
            state=PositionState.CANDIDATE,
            features=inp.features,
            detected_at=datetime.now(_TZ_CN).isoformat(timespec="seconds"),
        )

    async def ground_with_rag(
        self,
        candidate: CandidatePosition,
        db,
        *,
        llm=None,
    ) -> CandidatePosition:
        """RAG 接地（阶段二）：检索权威岗位库 + 种子列表匹配，生成定义草案。

        Args:
            candidate: candidate 池中的岗位
            db: PostgreSQL 会话（occupations 权威库检索）
            llm: LLMProviderChain（可选，定义草案 LLM 生成）

        Returns:
            更新 seed_matched / rag_matched / definition_draft 后的副本
        """
        from app.services.discovery.grounding import ground_with_rag as _ground

        result = await _ground(candidate.position_name, db, llm=llm)
        return candidate.model_copy(
            update={
                "seed_matched": result.seed_matched,
                "rag_matched": result.rag_matched,
                "definition_draft": result.definition,
            }
        )
