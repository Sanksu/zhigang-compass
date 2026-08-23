import { describe, expect, it } from 'vitest'

import type { BackendGapItem, MatchResult } from '@/components/match/types'
import {
  completedSkillsFromMatrix,
  gapDisplayType,
  proficiencyNumber,
  withRefreshedGaps,
} from './resume-match-adapters'

describe('proficiencyNumber', () => {
  it.each([
    ['了解', 1],
    ['熟悉', 2],
    ['精通', 3],
  ] as const)('maps candidate proficiency %s to %d', (level, expected) => {
    expect(proficiencyNumber(level, 'candidate', 0)).toBe(expected)
  })

  it.each([
    ['初级', 1],
    ['中级', 2],
    ['高级', 3],
    ['专家', 4],
  ] as const)('maps required proficiency %s to %d', (level, expected) => {
    expect(proficiencyNumber(level, 'requirement', 0)).toBe(expected)
  })

  it('uses the supplied fallback for missing, unrestricted, and mismatched vocabulary', () => {
    expect(proficiencyNumber(undefined, 'candidate', 0)).toBe(0)
    expect(proficiencyNumber('不限', 'requirement', 4)).toBe(4)
    expect(proficiencyNumber('高级', 'candidate', 0)).toBe(0)
    expect(proficiencyNumber('精通', 'requirement', 0)).toBe(0)
  })
})

describe('gapDisplayType', () => {
  it.each([
    ['missing', 'must', 'missing_must'],
    ['missing', 'nice', 'missing_nice'],
    ['weak', 'must', 'level_gap'],
    ['weak', 'nice', 'level_gap'],
    ['matched', 'must', 'matched'],
    ['matched', 'nice', 'matched'],
  ] as const)('maps %s/%s to %s', (status, necessity, expected) => {
    expect(gapDisplayType(status, necessity)).toBe(expected)
  })
})

const baseResult: MatchResult = {
  position_id: 'position-1',
  position_name: '前端工程师',
  total_score: 0.5,
  must_score: 0.5,
  nice_score: 0.5,
  exp_score: 0.5,
  summary: '测试结果',
  radar: [],
  gaps: [{
    skill: '旧技能', gap_type: 'missing_must', match_status: 'missing', priority: 'high',
    current_level: '未掌握', required_level: '高级',
  }],
  skill_matrix: [{
    skill: '旧技能', candidate_level: 0, required_level: 3, candidate_label: '未掌握',
    required_label: '高级', necessity: 'must', status: 'missing',
  }],
  learning_path: [],
  evidence_refs: [],
}

const refreshedGaps: BackendGapItem[] = [
  {
    skill: 'React', necessity: 'must', gap_type: 'weak', weight: 1, priority: 'high',
    current_proficiency: '熟悉', required_proficiency: '高级',
  },
  {
    skill: 'TypeScript', necessity: 'must', gap_type: 'matched', weight: 1, priority: 'medium',
    current_proficiency: '精通', required_proficiency: '高级',
  },
  {
    skill: '测试', necessity: 'nice', gap_type: 'missing', weight: 1, priority: 'low',
    current_proficiency: null, required_proficiency: '初级',
  },
]

describe('withRefreshedGaps', () => {
  it('replaces stale matrix and gaps with the newest missing, weak, and matched statuses', () => {
    const refreshed = withRefreshedGaps(baseResult, refreshedGaps)

    expect(refreshed.gaps).toEqual(expect.arrayContaining([
      expect.objectContaining({ skill: 'React', match_status: 'weak', gap_type: 'level_gap' }),
      expect.objectContaining({ skill: 'TypeScript', match_status: 'matched', gap_type: 'matched' }),
      expect.objectContaining({ skill: '测试', match_status: 'missing', gap_type: 'missing_nice' }),
    ]))
    expect(refreshed.skill_matrix).toEqual([
      expect.objectContaining({ skill: 'React', status: 'weak', candidate_level: 2, required_level: 3 }),
      expect.objectContaining({ skill: 'TypeScript', status: 'matched', candidate_level: 3, required_level: 3 }),
      expect.objectContaining({ skill: '测试', status: 'missing', candidate_level: 0, required_level: 1 }),
    ])
    expect(refreshed.skill_matrix).not.toContainEqual(expect.objectContaining({ skill: '旧技能' }))
  })

  it('derives completed learning-path skills from the refreshed matched state only', () => {
    const refreshed = withRefreshedGaps(baseResult, refreshedGaps)

    expect(completedSkillsFromMatrix(refreshed.skill_matrix)).toEqual(['TypeScript'])
  })
})
