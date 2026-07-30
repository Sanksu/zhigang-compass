/**
 * 简历匹配 mock 数据与类型 — 设计文档 §9 人岗匹配算法 + §10.4 简历解析与匹配
 *
 * 后端 API（M4 交付）：
 * - POST /api/v1/resume/parse → 返回 task_id
 * - POST /api/v1/match/recommend → 异步推荐 Top-N
 * - POST /api/v1/match/compare → 同步人岗比对
 * - GET /api/v1/match/result/{id}/gap → 差距分析
 * - GET /api/v1/match/result/{id}/path → 学习路径
 */

/** Top-N 推荐结果项 */
export interface RecommendItem {
  position_id: string
  position_name: string
  total_score: number
  must_score: number
  nice_score: number
  exp_score: number
  summary: string
  /** 岗位状态 */
  status: 'stable' | 'emerging' | 'declining'
  /** 关键差距（前 3 条） */
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
  gap_type: 'missing_must' | 'level_gap' | 'missing_nice'
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
  radar: RadarDimension[]
  skill_matrix: SkillMatrixItem[]
  gaps: GapItem[]
  learning_path: LearningPathItem[]
  /** 证据引用（设计文档要求 100% 覆盖率） */
  evidence_refs: { skill: string; source: string; url: string; confidence: number }[]
}

/** 简历解析后的候选人信息 */
export interface CandidateProfile {
  name: string
  total_years: number
  education: string
  school_tier: string
  skills: { name: string; level: '精通' | '熟练' | '了解'; years: number }[]
  projects: { name: string; stack: string; description: string }[]
}

/** mock 候选人画像 */
export const MOCK_CANDIDATE: CandidateProfile = {
  name: '张三',
  total_years: 4,
  education: '本科',
  school_tier: '211',
  skills: [
    { name: 'JavaScript', level: '熟练', years: 4 },
    { name: 'TypeScript', level: '熟练', years: 2 },
    { name: 'React', level: '熟练', years: 3 },
    { name: 'Vue', level: '了解', years: 1 },
    { name: 'Node.js', level: '熟练', years: 2 },
    { name: 'CSS', level: '熟练', years: 4 },
    { name: 'HTML', level: '精通', years: 4 },
    { name: 'Git', level: '熟练', years: 4 },
    { name: 'Python', level: '了解', years: 1 },
    { name: 'Docker', level: '了解', years: 1 },
  ],
  projects: [
    { name: '电商平台前端重构', stack: 'React + TypeScript + Tailwind', description: '主导迁移 jQuery 至 React 19，性能提升 40%' },
    { name: '内部后台系统', stack: 'Vue 3 + Vite + Pinia', description: '从 0 搭建组件库与权限体系' },
  ],
}

/** mock Top-N 推荐（5 个岗位） */
export const MOCK_RECOMMENDATIONS: RecommendItem[] = [
  {
    position_id: 'pos_0001',
    position_name: '前端开发工程师',
    total_score: 0.87,
    must_score: 0.92,
    nice_score: 0.78,
    exp_score: 0.90,
    summary: '技能高度匹配，必备技能覆盖率 92%，仅 Vue 熟练度不足',
    status: 'stable',
    key_gaps: ['Vue 熟练度需提升至熟练级', 'WebGL 缺失（nice 技能）'],
  },
  {
    position_id: 'pos_0002',
    position_name: '全栈工程师',
    total_score: 0.76,
    must_score: 0.82,
    nice_score: 0.70,
    exp_score: 0.75,
    summary: '前端扎实，后端技能（Python/SQL）需补强',
    status: 'stable',
    key_gaps: ['Python 仅了解级，需达熟练', 'SQL 缺失（must）', 'PostgreSQL 缺失'],
  },
  {
    position_id: 'pos_0004',
    position_name: 'AI 应用工程师',
    total_score: 0.58,
    must_score: 0.52,
    nice_score: 0.65,
    exp_score: 0.55,
    summary: 'AI 方向转型机会，需补齐 LangChain/Prompt 设计等核心技能',
    status: 'emerging',
    key_gaps: ['LangChain 缺失（must）', 'Prompt 设计 缺失（must）', 'PyTorch 缺失'],
  },
  {
    position_id: 'pos_0003',
    position_name: '后端开发工程师',
    total_score: 0.48,
    must_score: 0.42,
    nice_score: 0.55,
    exp_score: 0.50,
    summary: '后端岗位匹配度较低，建议先补 Python + SQL 基础',
    status: 'stable',
    key_gaps: ['Python 仅了解级', 'SQL 缺失（must）', 'Java 缺失', 'FastAPI 缺失'],
  },
  {
    position_id: 'pos_0005',
    position_name: '算法工程师',
    total_score: 0.32,
    must_score: 0.25,
    nice_score: 0.40,
    exp_score: 0.30,
    summary: '算法岗差距较大，需系统学习机器学习与 PyTorch',
    status: 'stable',
    key_gaps: ['PyTorch 缺失（must）', '机器学习 缺失（must）', '数学基础需补强'],
  },
]

/** 生成完整匹配结果（基于 position_id） */
export function getMockMatchResult(positionId: string): MatchResult {
  // 简化实现：所有岗位共用一份 mock，按 position_id 调整分数
  const rec = MOCK_RECOMMENDATIONS.find((r) => r.position_id === positionId)
  if (!rec) throw new Error(`未知 position_id: ${positionId}`)

  return {
    position_id: rec.position_id,
    position_name: rec.position_name,
    total_score: rec.total_score,
    must_score: rec.must_score,
    nice_score: rec.nice_score,
    exp_score: rec.exp_score,
    radar: [
      { name: '必备技能', candidate: rec.must_score * 100, required: 100 },
      { name: '加分技能', candidate: rec.nice_score * 100, required: 80 },
      { name: '工作经验', candidate: rec.exp_score * 100, required: 85 },
      { name: '学历背景', candidate: 80, required: 75 },
      { name: '项目经验', candidate: 75, required: 70 },
    ],
    skill_matrix: [
      { skill: 'JavaScript', candidate_level: 3, required_level: 3, necessity: 'must', match: 'full' },
      { skill: 'TypeScript', candidate_level: 2, required_level: 3, necessity: 'must', match: 'partial' },
      { skill: 'React', candidate_level: 2, required_level: 3, necessity: 'must', match: 'partial' },
      { skill: 'CSS', candidate_level: 2, required_level: 2, necessity: 'must', match: 'full' },
      { skill: 'HTML', candidate_level: 3, required_level: 3, necessity: 'must', match: 'full' },
      { skill: 'Vue', candidate_level: 1, required_level: 2, necessity: 'nice', match: 'partial' },
      { skill: 'Node.js', candidate_level: 2, required_level: 2, necessity: 'nice', match: 'full' },
      { skill: 'WebGL', candidate_level: 0, required_level: 2, necessity: 'nice', match: 'missing' },
      { skill: 'Git', candidate_level: 2, required_level: 2, necessity: 'must', match: 'full' },
      { skill: 'Docker', candidate_level: 1, required_level: 2, necessity: 'nice', match: 'partial' },
    ],
    gaps: [
      { skill: 'TypeScript', gap_type: 'level_gap', priority: 'high', current_level: '熟练', required_level: '精通' },
      { skill: 'React', gap_type: 'level_gap', priority: 'high', current_level: '熟练', required_level: '精通' },
      { skill: 'Vue', gap_type: 'level_gap', priority: 'medium', current_level: '了解', required_level: '熟练' },
      { skill: 'WebGL', gap_type: 'missing_nice', priority: 'low', current_level: '未掌握', required_level: '熟练' },
      { skill: 'Docker', gap_type: 'level_gap', priority: 'low', current_level: '了解', required_level: '熟练' },
    ],
    learning_path: [
      {
        skill: 'TypeScript 进阶',
        duration_days: 21,
        start_offset: 0,
        prerequisites: [],
        courses: [
          { title: 'TypeScript 高级编程', platform: 'Coursera', hours: 40 },
          { title: 'TS 类型体操实战', platform: '中国大学MOOC', hours: 15 },
        ],
        priority: 'high',
      },
      {
        skill: 'React 深入',
        duration_days: 18,
        start_offset: 21,
        prerequisites: ['TypeScript 进阶'],
        courses: [
          { title: 'React 源码剖析', platform: 'edX', hours: 32 },
          { title: 'React 性能优化实战', platform: 'Coursera', hours: 20 },
        ],
        priority: 'high',
      },
      {
        skill: 'Vue 基础',
        duration_days: 14,
        start_offset: 39,
        prerequisites: [],
        courses: [
          { title: 'Vue 3 官方教程', platform: '中国大学MOOC', hours: 24 },
        ],
        priority: 'medium',
      },
      {
        skill: 'Docker 入门',
        duration_days: 10,
        start_offset: 53,
        prerequisites: [],
        courses: [
          { title: 'Docker 容器化实战', platform: 'Coursera', hours: 18 },
        ],
        priority: 'low',
      },
      {
        skill: 'WebGL 基础',
        duration_days: 16,
        start_offset: 63,
        prerequisites: ['JavaScript 进阶'],
        courses: [
          { title: 'WebGL 编程指南', platform: 'edX', hours: 30 },
        ],
        priority: 'low',
      },
    ],
    evidence_refs: [
      { skill: 'React', source: 'BOSS直聘', url: 'https://example.com/jd/123', confidence: 0.95 },
      { skill: 'TypeScript', source: 'Stack Overflow', url: 'https://example.com/so/ts', confidence: 0.88 },
      { skill: 'Vue', source: 'GitHub', url: 'https://example.com/gh/vue', confidence: 0.82 },
    ],
  }
}
