/**
 * 匹配类型定义 — 设计文档 §9 人岗匹配算法 + §10.4 简历解析与匹配
 *
 * 数据来源：真实后端 API
 * - POST /api/v1/resume/parse → 上传触发异步解析
 * - GET  /api/v1/resume/list   → 已解析简历列表
 * - POST /api/v1/match/recommend → Top-N 推荐
 * - POST /api/v1/match/compare → 单点人岗比对
 */

/** 后端 match 返回的岗位评分项（recommend / compare 共用） */
export interface BackendMatchResult {
  position_id: string
  position_name: string
  total_score: number
  must_score: number
  nice_score: number
  exp_score: number
  matched_must: string[]
  missing_must: string[]
  summary: string
  unqualified: boolean
  /** 结果快照 ID（compare 同步执行后持久化，供 /match/result|gap|path|feedback 查询） */
  match_id?: string
  /** compare 专属：差距三态（missing/weak/matched，按优先级排序） */
  gaps?: BackendGapItem[]
  /** compare 专属：学习路径（missing/weak 技能的先修链 + 课程 Top-3） */
  learning_path?: BackendLearningPathItem[]
  /** compare 专属：证据引用（技能 → 原始 JD，图谱 MENTIONED_IN 链路） */
  evidence_refs?: BackendEvidenceRef[]
}

/** 后端证据引用项 */
export interface BackendEvidenceRef {
  skill: string
  source: string
  url: string
  confidence: number
}

/** 后端差距项 */
export interface BackendGapItem {
  skill: string
  skill_id?: string | null
  necessity: 'must' | 'nice'
  gap_type: 'missing' | 'weak' | 'matched'
  weight: number
  priority: 'high' | 'medium' | 'low'
  current_proficiency?: string | null
  required_proficiency?: string | null
}

/** 后端学习路径项 */
export interface BackendLearningPathItem {
  skill: string
  skill_id?: string | null
  prerequisites: string[]
  courses: BackendCourseRecommendation[]
  estimated_hours: number
  priority: 'high' | 'medium' | 'low'
}

/** 后端课程推荐 */
export interface BackendCourseRecommendation {
  course_id: string
  title: string
  platform: string
  quality_score?: number | null
  recommended: boolean
  source_url: string
  hours?: number | null
}

/** 已解析简历摘要（GET /resume/list） */
export interface ResumeSummary {
  id: string
  file_name: string
  skills: string[]
  total_years: number
  education_level?: string | null
  updated_at?: string | null
}

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
