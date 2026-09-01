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
  /** 岗位职能域（域治理成果接入；无域为 null/undefined 不渲染） */
  domain_name?: string | null
  total_score: number
  /** 岗位无必备技能门槛时为 null（A1 口径：无信息不判分，总分重归一） */
  must_score: number | null
  nice_score: number
  exp_score: number
  summary: string
  /** 岗位状态（后端未产出，按分数映射展示：≥0.6 stable / ≥0.4 declining / <0.4 low） */
  status: 'stable' | 'emerging' | 'declining' | 'low'
  /** 关键差距（前 3 条，来自 missing_must） */
  key_gaps: string[]
  /** JD 级证据（阶段 B：命中岗位族内原生 JD 二次精排 Top-2） */
  jd_evidence: {
    jd_title: string
    source: string
    source_url: string
    coverage: number
    hit_count: number
    must_total: number
    nice_total: number
    hit_skills: string[]
  }[]
}

/** 五维雷达图数据 */
export interface RadarDimension {
  name: string
  /** 候选人得分 0-100 */
  candidate: number
  /** 岗位要求 0-100 */
  required: number
}

/** 技能矩阵热力图项（直接映射后端 GapSkill） */
export interface SkillMatrixItem {
  skill: string
  /** 候选人熟练度数值（0-4，用于热力图色阶） */
  candidate_level: number
  /** 岗位要求熟练度数值（0-4，用于热力图色阶） */
  required_level: number
  /** 后端返回的熟练度展示值 */
  candidate_label: string
  required_label: string
  /** 必要性 */
  necessity: 'must' | 'nice'
  /** 后端差距状态 */
  status: 'missing' | 'weak' | 'matched'
}

/** 差距分析项 */
export interface GapItem {
  skill: string
  gap_type: 'missing_must' | 'level_gap' | 'missing_nice' | 'matched'
  /** 后端权威差距状态，前端不得根据展示刻度重新判定 */
  match_status: 'missing' | 'weak' | 'matched'
  priority: 'high' | 'medium' | 'low'
  current_level: string
  required_level: string
  /** 软技能标记（责任心/沟通能力等软素质，仅展示打标不影响评分） */
  is_soft?: boolean
  // ── 契约扩展字段（#341 起后端全量返回，可选——图谱不可用时后端回填补齐，
  //    前端不再本地推导/mock；对应契约 GapSkill schema）──
  /** 市场需求度 0-1 */
  demand?: number
  /** 需求趋势 -1..1（契约描述；实现现口径为 0..1 扩散度，见审查报告 L-13 待裁决） */
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
  courses: { title: string; platform: string; hours: number; url?: string }[]
  /** 优先级 */
  priority: 'high' | 'medium' | 'low'
  // ── 契约字段（对应契约 LearningPathItem schema：status/estimated_hours
  //    后端返回；本接口沿用驼峰命名由 toLearningPath 映射）──
  /** 学习状态（done=已掌握 / doing=下一步 / locked=未解锁） */
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
  /** 岗位无必备技能门槛时为 null（A1 口径：无信息不判分，总分重归一） */
  must_score: number | null
  nice_score: number
  exp_score: number
  summary: string
  radar: RadarDimension[]
  skill_matrix: SkillMatrixItem[]
  gaps: GapItem[]
  learning_path: LearningPathItem[]
  /** 学习路径是否因领域跨簇语义黑名单拦截（P1：跨域诱导组合拒绝生成） */
  learning_path_blocked?: boolean
  /** 拦截原因（命中的岗位行业 × 候选人领域对），未拦截为 null/undefined */
  learning_path_block_reason?: string | null
  /** 证据引用（设计文档要求 100% 覆盖率） */
  evidence_refs: { skill: string; source: string; url: string; confidence: number }[]
  /** 岗位职能域（域治理成果接入；无域/查询失败为 null 不渲染） */
  domain_name?: string | null
  /** 实际评分权重（BT v3：configs/match_weights.json） */
  weights?: { must?: number; nice?: number; exp?: number } | null
  /** JD 级评分溯源：total_score 为同岗 jd_compared 条 JD 中的最高分 */
  jd_compared?: number | null
  /** 最佳匹配 JD 原文（compare 返回；旧快照/最佳 JD 行已删除为 null） */
  jd_original?: {
    jd_title: string
    source: string
    source_url: string
    text: string
    /** 该 JD 的匹配得分（=同岗最高分 total_score，评分溯源） */
    score?: number
  } | null
}

/** 简历解析后的候选人信息（页面摘要展示） */
export interface CandidateProfile {
  name: string
  total_years: number
  education: string
  skills: { name: string; level: string }[]
}
