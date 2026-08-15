"""新岗位发现数据模型（设计文档 7.2 节）。

岗位六状态机（§7.2.1）+ 4 核心特征 + 2 辅助加分特征（§7.2.2）。
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class PositionState(str, Enum):
    """岗位生命周期状态机（设计文档 7.2.1 节，六状态）。

    active 为图谱常态（import_jd/聚合产生，有 JD 证据支撑），不属于发现
    状态机——发现流程只操作 candidate/emerging/stable/declining（08-15 语义
    修正：此前 import_jd 新岗位默认 candidate 与发现候选混淆）。
    """
    ACTIVE = "active"          # 常态：图谱正常岗位（import_jd/聚合产生）
    CANDIDATE = "candidate"    # 候选态：趋势监测命中规则门控（发现候选，persist 镜像）
    EMERGING = "emerging"      # 新兴态：跨 ≥2 源验证 + 置信度 ≥ 0.6（admin 审核）
    STABLE = "stable"          # 稳定态：jd_count ≥ 5 + 源 ≥ 2 + 波动 < 25% + novelty < 0.2（§7.2.1）
    DECLINING = "declining"    # 衰退态：连续 3 窗口频次下降 > 40%
    ARCHIVED = "archived"      # 归档态：admin 确认衰退（终态）
    REJECTED = "rejected"      # 驳回态：admin 驳回 candidate（终态，不入图谱）


class DiscoveryFeatures(BaseModel):
    """新岗位发现特征（设计文档 7.2.2 节）。

    4 项核心特征（JD 源驱动，Z-score 门控主判定）+ 2 项辅助加分特征（M4 上线）。
    """
    # ── 核心特征（4 项，仅 JD 源驱动）──
    jd_freq_ma3: float = Field(description="JD 频次 3 月移动平均")
    z_score: Optional[float] = Field(default=None, description="JD 频次 Z-score，主判定信号")
    source_diversity: int = Field(description="JD 独立源计数")
    cross_source_consistency: Optional[float] = Field(default=None, description="三源 JD 语义相似度均值")

    # ── 辅助加分特征（2 项，M4 上线，candidate→emerging 置信度加成）──
    arxiv_paper_count: Optional[int] = Field(default=None, description="arXiv 周论文数（M4）")
    github_star_velocity: Optional[float] = Field(default=None, description="GitHub Star 增速标准分（M4）")

    # ── 成熟岗位排除（2026-08-11）──
    first_seen_date: Optional[str] = Field(default=None, description="岗位首次观测日期（ISO，早于观测起点视为存量成熟岗位）")


class ConfidenceScore(BaseModel):
    """综合置信度（设计文档 7.2.4 节）。"""
    base_confidence: float = Field(ge=0.0, le=1.0, description="三维加权基础置信度")
    arxiv_anomaly: bool = Field(default=False, description="arXiv 论文数 δ > 2σ")
    github_anomaly: bool = Field(default=False, description="GitHub Star 增速 δ > 2σ")
    bonus: float = Field(default=0.0, description="学术/社区加分（+0.10 单异常 / +0.15 双异常）")
    final_confidence: float = Field(ge=0.0, le=1.0, description="封顶后的最终置信度")


class CandidatePosition(BaseModel):
    """候选岗位（设计文档 7.2.3 节判定流程输出）。"""
    candidate_id: str
    position_name: str
    state: PositionState = PositionState.CANDIDATE
    features: DiscoveryFeatures
    confidence: Optional[ConfidenceScore] = None
    detected_at: str = Field(description="进入 candidate 池的时间 ISO8601")
    evidence_refs: list[str] = Field(default_factory=list, description="证据 JD 的 evidence_id 列表")
    seed_matched: bool = Field(default=False, description="是否匹配预置种子列表")
    rag_matched: bool = Field(default=False, description="是否匹配权威岗位库（RAG 接地）")
    definition_draft: str = Field(default="", description="RAG 阶段二生成的岗位定义草案（种子/权威库命中时）")


class RagGroundingResult(BaseModel):
    """RAG 接地结果（阶段二输出，设计文档 7.2.3 节）。

    权威库命中时由 `occupations` 表 + 种子列表产出定义草案；
    LLM 生成失败不影响接地判定（草案可缺省，仅记录匹配状态）。
    """
    matched: bool = Field(default=False, description="是否命中种子列表或权威库")
    seed_matched: bool = Field(default=False, description="命中预置种子列表")
    rag_matched: bool = Field(default=False, description="命中权威岗位库（O*NET occupations 表）")
    matched_name: str = Field(default="", description="命中的权威岗位名（英文）或种子名")
    occupation_code: str = Field(default="", description="命中的 O*NET-SOC 代码，种子命中时为空")
    definition: str = Field(default="", description="岗位定义草案（LLM 生成或权威库原文兜底）")
