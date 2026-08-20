/**
 * 学习路径时间轴 — 拓扑分层 & 状态推导纯函数单测
 *
 * 覆盖 buildTimelineMilestones：
 *  - 空输入/单根/链式先修/fan-out/未知先修
 *  - done(done)/doing/doing(locked) 推导
 *  - 学时兜底（duration_days×8）与 coursesCount
 *  - 阶段分层（depth）与层内排序（高优优先、名称）
 */
import { describe, expect, it } from 'vitest'
import { buildTimelineMilestones, buildDagGraph, type TimelineMilestone } from './learning-timeline'
import type { LearningPathItem } from '@/components/match/types'

function item(partial: Partial<LearningPathItem> & { skill: string }): LearningPathItem {
  return {
    duration_days: 1,
    start_offset: 0,
    prerequisites: [],
    courses: [],
    priority: 'medium',
    ...partial,
  }
}

function statuses(ms: TimelineMilestone[]): Map<string, string> {
  const map = new Map<string, string>()
  for (const m of ms) for (const t of m.tasks) map.set(t.skill, t.status)
  return map
}

describe('buildTimelineMilestones', () => {
  it('空输入返回空数组', () => {
    expect(buildTimelineMilestones([])).toEqual([])
  })

  it('单根技能：深度 1，无已掌握时为 doing', () => {
    const ms = buildTimelineMilestones([item({ skill: 'React' })])
    expect(ms).toHaveLength(1)
    expect(ms[0].depth).toBe(1)
    expect(ms[0].tasks[0].skill).toBe('React')
    expect(ms[0].tasks[0].status).toBe('doing')
  })

  it('链式先修 A→B→C：深度 1/2/3，仅 A 为 doing', () => {
    const a = item({ skill: 'A' })
    const b = item({ skill: 'B', prerequisites: ['A'] })
    const c = item({ skill: 'C', prerequisites: ['B'] })
    const ms = buildTimelineMilestones([a, b, c])
    const st = statuses(ms)
    expect(ms.map((m) => m.depth)).toEqual([1, 2, 3])
    expect(st.get('A')).toBe('doing')
    expect(st.get('B')).toBe('locked')
    expect(st.get('C')).toBe('locked')
  })

  it('已掌握 A 后：B 变为 doing，C 仍 locked', () => {
    const a = item({ skill: 'A' })
    const b = item({ skill: 'B', prerequisites: ['A'] })
    const c = item({ skill: 'C', prerequisites: ['B'] })
    const ms = buildTimelineMilestones([a, b, c], ['A'])
    const st = statuses(ms)
    expect(st.get('A')).toBe('done')
    expect(st.get('B')).toBe('doing')
    expect(st.get('C')).toBe('locked')
  })

  it('已掌握 A、B 后：C 变为 doing', () => {
    const ms = buildTimelineMilestones(
      [
        item({ skill: 'A' }),
        item({ skill: 'B', prerequisites: ['A'] }),
        item({ skill: 'C', prerequisites: ['B'] }),
      ],
      ['A', 'B'],
    )
    expect(statuses(ms).get('C')).toBe('doing')
  })

  it('fan-out：共同先修 A 后接 B、C，两者同在阶段 2', () => {
    const a = item({ skill: 'A' })
    const b = item({ skill: 'B', prerequisites: ['A'] })
    const c = item({ skill: 'C', prerequisites: ['A'] })
    const ms = buildTimelineMilestones([a, b, c])
    const depths = ms.map((m) => [m.depth, m.tasks.map((t) => t.skill)])
    expect(depths).toEqual([
      [1, ['A']],
      [2, ['B', 'C']],
    ])
    expect(statuses(ms).get('B')).toBe('locked')
    expect(statuses(ms).get('C')).toBe('locked')
  })

  it('先修不在列表内视为已满足，不阻断分层与推进', () => {
    const x = item({ skill: 'X', prerequisites: ['ghost-skill'] })
    const ms = buildTimelineMilestones([x])
    expect(ms[0].depth).toBe(1)
    expect(ms[0].tasks[0].status).toBe('doing')
  })

  it('学时兜底 duration_days×8；提供了 estimatedHours 则优先使用', () => {
    const a = buildTimelineMilestones([item({ skill: 'A', duration_days: 3 })])
    expect(a[0].tasks[0].estimatedHours).toBe(24)
    const b = buildTimelineMilestones([item({ skill: 'B', duration_days: 3, estimatedHours: 10 })])
    expect(b[0].tasks[0].estimatedHours).toBe(10)
  })

  it('coursesCount 来自 courses 数组长度', () => {
    const ms = buildTimelineMilestones([
      item({ skill: 'A', courses: [{ title: 'c1', platform: 'p', hours: 2 }, { title: 'c2', platform: 'p', hours: 3 }] }),
    ])
    expect(ms[0].tasks[0].coursesCount).toBe(2)
  })

  it('层内排序：高优优先，其次名称', () => {
    const low = item({ skill: 'Z', priority: 'low' })
    const high = item({ skill: 'A', priority: 'high' })
    // 同层无先修：A(high) 优先于 Z(low) → 排序 ['A', 'Z']
    const ms = buildTimelineMilestones([low, high])
    expect(ms[0].tasks.map((t) => t.skill)).toEqual(['A', 'Z'])
  })
})

describe('buildDagGraph', () => {
  it('链式 A→B→C：节点带分层/状态，先修边正确', () => {
    const { nodes, links } = buildDagGraph([
      item({ skill: 'A' }),
      item({ skill: 'B', prerequisites: ['A'] }),
      item({ skill: 'C', prerequisites: ['B'] }),
    ])
    expect(nodes.map((n) => n.id)).toEqual(['A', 'B', 'C'])
    expect(nodes.map((n) => n.layer)).toEqual([1, 2, 3])
    expect(nodes.find((n) => n.id === 'A')?.status).toBe('doing')
    expect(nodes.find((n) => n.id === 'C')?.status).toBe('locked')
    expect(links).toEqual([
      { source: 'A', target: 'B' },
      { source: 'B', target: 'C' },
    ])
  })

  it('仅连接两端都在路径内的边（未知先修被忽略）', () => {
    const { links } = buildDagGraph([item({ skill: 'X', prerequisites: ['ghost'] })])
    expect(links).toEqual([])
  })

  it('已掌握 A 后：B 节点为 doing，DAG 状态与时间轴一致', () => {
    const { nodes } = buildDagGraph(
      [
        item({ skill: 'A' }),
        item({ skill: 'B', prerequisites: ['A'] }),
        item({ skill: 'C', prerequisites: ['B'] }),
      ],
      ['A'],
    )
    const st = Object.fromEntries(nodes.map((n) => [n.id, n.status]))
    expect(st).toEqual({ A: 'done', B: 'doing', C: 'locked' })
  })
})