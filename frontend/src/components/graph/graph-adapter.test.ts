import { describe, expect, it } from 'vitest'
import type { components } from '@/types/api'
import { toGraphData } from './graph-adapter'

type GraphViewData = components['schemas']['GraphViewData']

function graphView(nodes: GraphViewData['nodes']): GraphViewData {
  return {
    view_type: 'panorama',
    nodes,
    edges: [
      { source: 'pos-active', target: 'skill-soft', necessity: 'nice', weight: 0.4 },
    ],
    stats: { nodes: nodes.length, edges: 1, total_nodes: 18, total_edges: 1 },
  }
}

describe('toGraphData', () => {
  it('保留 active 岗位状态与软技能类目', () => {
    const data = toGraphData(graphView([
      { id: 'pos-active', name: '数据运营', type: 'position', status: 'active' },
      { id: 'skill-soft', name: '沟通能力', type: 'skill', skill_category: '软技能' },
    ]))

    expect(data.nodes[0]).toMatchObject({ status: 'active', value: 1 })
    expect(data.nodes[1]).toMatchObject({ skill_category: '软技能', value: 1 })
    expect(data.stats.totalNodesInGraph).toBe(18)
  })

  it('未知岗位状态回退为 candidate，技能不携带岗位状态', () => {
    const data = toGraphData(graphView([
      { id: 'pos-active', name: '未知岗位', type: 'position', status: 'unknown' as 'active' },
      { id: 'skill-soft', name: 'Python', type: 'skill', skill_category: '编程语言' },
    ]))

    expect(data.nodes[0].status).toBe('candidate')
    expect(data.nodes[1].status).toBeUndefined()
  })
})
