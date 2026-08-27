/** 演化域共享类型（从 evolution-page 抽出，第六轮审查拆分）。 */
import type { components } from '@/types/api'

// ===== Types =====

export type TrendTone = 'emerging' | 'declining' | 'stable'

export interface VersionDiffItem {
  id: string
  name: string
  type: 'position' | 'skill' | 'evidence' | 'course' | 'tool'
  change: 'added' | 'removed' | 'changed'
  detail: string
}

export interface MetricItem {
  key: string
  label: string
  value: string | number
  delta: number
  tone: TrendTone
  hint: string
}

/** 后端 /evolution/versions 返回项 */
export type EvolutionVersion = components['schemas']['EvolutionVersion']

/** 后端 /evolution/diff 返回的节点项（含真实名称） */
export type EvolutionDiffNode = components['schemas']['EvolutionDiffNode']

/** 后端 /evolution/diff 返回项 */
export type EvolutionDiff = components['schemas']['EvolutionDiff']


/** 后端 /evolution/signals 返回项（EvolutionSignal 序列化） */
export type EvolutionSignal = components['schemas']['EvolutionSignal']

export type EvolutionSignalsData = components['schemas']['EvolutionSignalsData']

/** 后端 /evolution/versions/{id} 返回的版本详情 */
export type EvolutionVersionDetail = components['schemas']['EvolutionVersionDetail']

/** 后端 /evolution/position/{id}/evolution 返回项 */
export type PositionEvolutionData = components['schemas']['PositionEvolutionData']

/** 后端 /evolution/positions 返回项（默认岗位演化列表） */
export type PositionEvolutionListData = components['schemas']['PositionEvolutionListData']

/** 后端 /evolution/events 返回的谱系事件项 */
export type EvolutionEvent = components['schemas']['EvolutionEvent']

/** 后端 /evolution/events 返回项 */
export type EvolutionEventListData = components['schemas']['EvolutionEventListData']

/** 后端 /evolution/skills 返回项 */
export type SkillEvolutionListData = components['schemas']['SkillEvolutionListData']

/** 后端 /evolution/skills 列表项（含快照点） */
export type SkillEvolutionData = components['schemas']['SkillEvolutionData']
