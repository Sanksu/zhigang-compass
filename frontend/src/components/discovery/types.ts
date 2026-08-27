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
