import type { components } from '@/types/api'
/**
 * 匹配类型定义 — 设计文档 §9 人岗匹配算法 + §10.4 简历解析与匹配
 *
 * 数据来源：真实后端 API
 * - POST /api/v1/resume/parse → 上传触发异步解析
 * - GET  /api/v1/resume/list   → 已解析简历列表
 * - POST /api/v1/match/recommend → Top-N 推荐
 * - POST /api/v1/match/compare → 单点人岗比对
 */

/** 后端匹配结果（契约 MatchResult，compare 同步 / result 快照共用） */
export type BackendMatchResult = components['schemas']['MatchResult']

/** 后端差距项（契约 GapSkill） */
export type BackendGapItem = components['schemas']['GapSkill']

/** 后端学习路径项（契约 LearningPathItem） */
export type BackendLearningPathItem = components['schemas']['LearningPathItem']

/** LLM 诊断报告（契约 DiagnosisReport，GET /match/result/{id}/diagnosis） */
export type BackendDiagnosisReport = components['schemas']['DiagnosisReport']

/** 已解析简历摘要（契约 ResumeSummaryItem，GET /resume/list） */
export type ResumeSummary = components['schemas']['ResumeSummaryItem']

/** Top-N 推荐结果项（页面展示） */
export interface RecommendItem {
  position_id: string
  position_name: string
  total_score: number
  must_score: number
  nice_score: number
  exp_score: number
  summary: string
  /** 岗位状态（后端未产出，按分数映射展示：≥0.6 stable / ≥0.4 declining / <0.4 low） */
  status: 'stable' | 'emerging' | 'declining' | 'low'
  /** 关键差距（前 3 条，来自 missing_must） */
  key_gaps: string[]
}

/** 五维雷达图数据 */
export interface RadarDimension {
  name: string
  /** 候选人得分 0-100 */
  candidate: number
  /** 岗位要求 0-100 */
  required: number
}

/** 技能矩阵热力图项 */
export interface SkillMatrixItem {
  skill: string
  /** 候选人熟练度 0-3（0=未掌握） */
  candidate_level: number
  /** 岗位要求熟练度 0-3 */
  required_level: number
  /** 必要性 */
  necessity: 'must' | 'nice'
  /** 匹配状态 */
  match: 'full' | 'partial' | 'missing'
}

/** 差距分析项 */
export interface GapItem {
  skill: string
  gap_type: 'missing_must' | 'level_gap' | 'missing_nice' | 'matched'
  priority: 'high' | 'medium' | 'low'
  current_level: string
  required_level: string
  // ── 差距分析数据升级新增（后端暂未直接返回，均为可选，缺省由 UI 推导/mock）──
  /** 市场需求度 0-1 */
  demand?: number
  /** 需求趋势 -1..1 */
  trend?: number
  /** ROI 指标 = (demand × trend) / cost，用于高杠杆缺口打标 */
  roi?: number
  /** 该技能是否高杠杆缺口（Top3 ROI） */
  high_roi?: boolean
  /** 评分/差距证据（供点击展开溯源） */
  evidence?: { role: 'jd' | 'resume'; text: string }[]
}

/** 学习路径项（甘特图） */
export interface LearningPathItem {
  skill: string
  /** 预计学习时长（天） */
  duration_days: number
  /** 起始偏移（天，从前一项结束开始） */
  start_offset: number
  /** 先修技能 */
  prerequisites: string[]
  /** 推荐课程 */
  courses: { title: string; platform: string; hours: number }[]
  /** 优先级 */
  priority: 'high' | 'medium' | 'low'
  // ── 学习路径双轨制新增（后端暂未直接返回，均为可选，缺失由 UI 兜底）──
  /** 学习状态（done=已掌握 / doing=下一步 / locked=未解锁），后端未返回时前端按拓扑推导 */
  status?: 'done' | 'doing' | 'locked'
  /** 预计学时（小时）。缺省由 duration_days×8 推导 */
  estimatedHours?: number
  /** 市场需求度 0-1（可选，供 ROI 打标） */
  demand?: number
  /** 需求趋势 -1..1（可选，供 ROI 打标） */
  trend?: number
  /** ROI 指标 = (demand × trend) / cost（可选，供高杠杆缺口复用） */
  roi?: number
  /** 评分/差距证据（可选，供数据溯源展开） */
  evidence?: string[]
}

/** 完整匹配结果（人岗比对） */
export interface MatchResult {
  position_id: string
  position_name: string
  total_score: number
  must_score: number
  nice_score: number
  exp_score: number
  summary: string
  radar: RadarDimension[]
  skill_matrix: SkillMatrixItem[]
  gaps: GapItem[]
  learning_path: LearningPathItem[]
  /** 证据引用（设计文档要求 100% 覆盖率） */
  evidence_refs: { skill: string; source: string; url: string; confidence: number }[]
}

/** 简历解析后的候选人信息（页面摘要展示） */
export interface CandidateProfile {
  name: string
  total_years: number
  education: string
  skills: { name: string; level: string }[]
}
