import type { BackendGapItem, GapItem, MatchResult, SkillMatrixItem } from '@/components/match/types'

export type BackendGapStatus = 'missing' | 'weak' | 'matched'
export type GapDisplayType = 'missing_must' | 'level_gap' | 'missing_nice' | 'matched'
type ProficiencySource = 'candidate' | 'requirement'

const CANDIDATE_PROFICIENCY_NUM: Record<string, number> = {
  了解: 1,
  熟悉: 2,
  精通: 3,
}

const REQUIRED_PROFICIENCY_NUM: Record<string, number> = {
  初级: 1,
  中级: 2,
  高级: 3,
  专家: 4,
}

/**
 * 将后端 GapSkill 的展示文案转换为热力图的数值刻度。
 *
 * 候选人 current_proficiency 使用简历侧 1-3 级语义（了解/熟悉/精通）；
 * 岗位 required_proficiency 使用岗位规范 1-4 级语义（初级/中级/高级/专家）。
 * 两个字段不可交叉解释，未知值按调用方提供的回退值处理。
 */
export function proficiencyNumber(
  text: string | null | undefined,
  source: ProficiencySource,
  fallback: number,
): number {
  const value = text?.trim()
  if (!value || value === '不限') return fallback

  const levels = source === 'candidate' ? CANDIDATE_PROFICIENCY_NUM : REQUIRED_PROFICIENCY_NUM
  return levels[value] ?? fallback
}

/** 将后端三态差距映射为前端展示分类。 */
export function gapDisplayType(status: BackendGapStatus, necessity: 'must' | 'nice'): GapDisplayType {
  if (status === 'matched') return 'matched'
  if (status === 'weak') return 'level_gap'
  return necessity === 'must' ? 'missing_must' : 'missing_nice'
}

/** 将后端 GapSkill 转换为页面差距行。 */
export function toGapItem(gap: BackendGapItem): GapItem {
  return {
    skill: gap.skill,
    gap_type: gapDisplayType(gap.gap_type, gap.necessity),
    match_status: gap.gap_type,
    priority: gap.priority,
    current_level: gap.current_proficiency ?? '未掌握',
    required_level: gap.required_proficiency ?? '不限',
    is_soft: gap.is_soft,
    demand: gap.demand,
    trend: gap.trend,
    roi: gap.roi,
    high_roi: gap.high_roi,
    evidence: gap.evidence?.map((e) => ({ role: e.role, text: e.text })),
  }
}

/** 将后端 GapSkill 转换为热力图矩阵项。 */
export function toSkillMatrixItem(gap: BackendGapItem): SkillMatrixItem {
  return {
    skill: gap.skill,
    candidate_level: proficiencyNumber(gap.current_proficiency, 'candidate', 0),
    required_level: proficiencyNumber(gap.required_proficiency, 'requirement', 0),
    candidate_label: gap.current_proficiency ?? '未掌握',
    required_label: gap.required_proficiency ?? '不限',
    necessity: gap.necessity,
    status: gap.gap_type,
  }
}

/**
 * 用同一份最新 gaps 原子替换所有依赖数据，避免保留旧技能矩阵，
 * 并使学习路径的已完成技能标记立即基于新的 matched 状态重算。
 */
export function withRefreshedGaps(
  result: MatchResult,
  gaps: BackendGapItem[],
): MatchResult {
  return {
    ...result,
    gaps: gaps.map(toGapItem),
    skill_matrix: gaps.map(toSkillMatrixItem),
  }
}

/** 从最新技能矩阵派生学习路径的已完成技能。 */
export function completedSkillsFromMatrix(skillMatrix: SkillMatrixItem[]): string[] {
  return skillMatrix.filter((skill) => skill.status === 'matched').map((skill) => skill.skill)
}
