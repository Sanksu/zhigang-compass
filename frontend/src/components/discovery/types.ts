/** 新岗位发现域共享类型（/discovery 页面）。 */
import type { components } from '@/types/api'

/** 后端 /discovery/recent 返回的技能项 */
export type DiscoverySkill = components['schemas']['DiscoverySkill']

/** 后端 /discovery/recent 返回的候选（含技能） */
export type RecentDiscoveryCandidate = components['schemas']['RecentDiscoveryCandidate']

/** 后端 /discovery/recent 返回 data */
export type DiscoveryRecentData = components['schemas']['DiscoveryRecentData']

/** 后端 /discovery/position-skills-delta 返回的技能增减项 */
export type PositionSkillsDelta = components['schemas']['PositionSkillsDelta']

/** 后端 /discovery/position-skills-delta 返回 data */
export type PositionSkillsDeltaData = components['schemas']['PositionSkillsDeltaData']

/** 岗位状态 → 展示标签/色阶（对齐 evolution/state 语义） */
export const DISCOVERY_STATE_LABEL: Record<string, string> = {
  candidate: '候选·待审核',
  emerging: '新兴',
  stable: '稳定',
  declining: '衰退',
  archived: '已归档',
  active: '活跃',
}

export const DISCOVERY_STATE_TONE: Record<string, 'emerging' | 'stable' | 'declining' | 'candidate' | 'archived'> = {
  candidate: 'candidate',
  emerging: 'emerging',
  stable: 'stable',
  declining: 'declining',
  active: 'stable',
  archived: 'archived',
}
