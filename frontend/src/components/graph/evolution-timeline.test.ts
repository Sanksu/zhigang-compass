/**
 * 演化时间轴纯函数单测：sortVersionsAsc（时间轴顺序）+ toEvolutionMarks
 * （相邻版本 diff → 画布打标/摘要 chips 数据）。
 */
import { describe, expect, it } from 'vitest'

import { sortVersionsAsc, toEvolutionMarks } from './evolution-timeline'
import type { components } from '@/types/api'

type EvolutionVersion = components['schemas']['EvolutionVersion']

function version(id: string, createdAt?: string | null): EvolutionVersion {
  return {
    version_id: id,
    created_at: createdAt ?? null,
    change_summary: `${id} 摘要`,
    triggered_by: null,
    node_added: 0,
    node_removed: 0,
    node_changed: 0,
  }
}

describe('sortVersionsAsc', () => {
  it('按 created_at 升序排列（滑轨从旧到新）', () => {
    const sorted = sortVersionsAsc([
      version('v3', '2026-08-22T02:00:00Z'),
      version('v1', '2026-08-20T02:00:00Z'),
      version('v2', '2026-08-21T02:00:00Z'),
    ])
    expect(sorted.map((v) => v.version_id)).toEqual(['v1', 'v2', 'v3'])
  })

  it('缺 created_at 的版本排尾（字符串比较空串居前不留洞）', () => {
    const sorted = sortVersionsAsc([version('v2', null), version('v1', '2026-08-20T00:00:00Z')])
    expect(sorted.map((v) => v.version_id)).toEqual(['v1', 'v2'])
  })
})

describe('toEvolutionMarks', () => {
  it('diff 增删集 → ids Set + 名称列表 + 日期标签', () => {
    const m = toEvolutionMarks(version('v9', '2026-08-22T16:00:00+08:00'), {
      nodes_added: [
        { id: 'sk_new', name: 'MCP', type: 'skill' },
        { id: 'pos_new', name: '智能体工程师', type: 'position' },
      ],
      nodes_removed: [{ id: 'sk_old', name: 'Ext.js', type: 'skill' }],
      nodes_changed: [],
      edges_added: [],
      edges_removed: [],
    })
    expect(m.addedIds).toEqual(new Set(['sk_new', 'pos_new']))
    expect(m.removedIds).toEqual(new Set(['sk_old']))
    expect(m.addedNames).toEqual(['MCP', '智能体工程师'])
    expect(m.removedNames).toEqual(['Ext.js'])
    expect(m.dateLabel).toBe('8/22')
    expect(m.summary).toBe('v9 摘要')
  })

  it('缺 created_at 时日期标签回落版本号前 8 位', () => {
    const m = toEvolutionMarks(version('v_20260822'), {
      nodes_added: [],
      nodes_removed: [],
      nodes_changed: [],
      edges_added: [],
      edges_removed: [],
    })
    expect(m.dateLabel).toBe('v_202608')
    expect(m.addedIds.size).toBe(0)
    expect(m.removedIds.size).toBe(0)
  })
})
