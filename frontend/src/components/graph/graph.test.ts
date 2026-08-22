/**
 * 图谱渲染纯函数单测 — 覆盖本轮显示逻辑优化新增的核心行为：
 * - graph-2d：技能标签密度阈值（中位数截断，低关联技能不常显）
 * - graph-3d：节点视觉半径（类型基础差 + 选中/展开放大）
 * - graph-utils：过滤打标（computeFilterMarks——压暗而非剔除，筛选时布局稳定）
 * - graph-layout：岗位防重叠（hasPositionOverlap 判定 + enforceSpread 强制分散，
 *   2026-08-15 重叠修复双保险的纯函数部分）
 */
import { describe, expect, it, vi } from 'vitest'
import { computeFilterMarks, skillLabelThreshold, nodeRadius } from './graph-utils'
import { enforceSpread, hasPositionOverlap } from './graph-layout'
import type { GraphEdge, GraphNode, PositionStatus } from './types'

function skill(id: string, value?: number): GraphNode {
  return { id, name: id, type: 'skill', value }
}

function position(id: string, value?: number): GraphNode {
  return { id, name: id, type: 'position', value }
}

/** 构造 ECharts chart 最小 fake：nodes/layouts 按引用维护，可断言写回结果 */
function makeChart(
  nodes: { type: string; symbolSize?: number }[],
  layouts: (number[] | undefined)[],
) {
  const layoutsCopy = layouts.map((l) => (l ? [...l] : undefined))
  let refreshed = 0
  const chart = {
    // getModel 可空（与 EChartsModel 最小类型一致：dispose 后返回 null）
    getModel: (): {
      getSeriesByIndex(index: number): { getData(): typeof list } | null
    } | null => ({
      getSeriesByIndex: () => ({ getData: () => list }),
    }),
    getZr: () => ({ refresh: () => void refreshed++ }),
    layouts: layoutsCopy,
    refreshed: () => refreshed,
  }
  const list = {
    count: () => nodes.length,
    getRawDataItem: (i: number) => nodes[i],
    getItemLayout: (i: number) => layoutsCopy[i],
    setItemLayout: (i: number, l: number[]) => {
      layoutsCopy[i] = [...l]
    },
  }
  return chart
}

describe('skillLabelThreshold', () => {
  it('无技能节点时返回 0（全部常显）', () => {
    expect(skillLabelThreshold([position('p1')])).toBe(0)
  })

  it('空数组返回 0', () => {
    expect(skillLabelThreshold([])).toBe(0)
  })

  it('技能 value 中位数作为标签显示阈值（奇数个取中间）', () => {
    const nodes = [skill('s1', 2), skill('s2', 5), skill('s3', 9)]
    expect(skillLabelThreshold(nodes)).toBe(5)
  })

  it('偶数个技能取上中位数（排序后中间偏右）', () => {
    const nodes = [skill('s1', 1), skill('s2', 4), skill('s3', 6), skill('s4', 10)]
    expect(skillLabelThreshold(nodes)).toBe(6)
  })

  it('岗位节点不参与阈值计算（仅统计技能节点）', () => {
    const nodes = [position('p1', 999), skill('s1', 3), skill('s2', 7)]
    expect(skillLabelThreshold(nodes)).toBe(7)
  })

  it('value 缺失的技能按 0 参与排序', () => {
    const nodes = [skill('s1'), skill('s2', 8)]
    expect(skillLabelThreshold(nodes)).toBe(8)
  })

  it('稠密图（>60 技能）取 75 分位——band1 只亮头部技能标签（P0-3 毛发球治理）', () => {
    // 80 个技能 value 1..80：75 分位 = 61（中位数口径则会是 41，放出一半标签）
    const nodes = Array.from({ length: 80 }, (_, i) => skill(`s${i}`, i + 1))
    expect(skillLabelThreshold(nodes)).toBe(61)
  })
})

describe('nodeRadius', () => {
  it('岗位基础半径大于技能，技能大于证据', () => {
    const pos = position('p1', 0)
    const sk = skill('s1', 0)
    const ev: GraphNode = { id: 'e1', name: 'e1', type: 'evidence', value: 0 }
    const rp = nodeRadius(pos, false, false)
    const rs = nodeRadius(sk, false, false)
    const re = nodeRadius(ev, false, false)
    expect(rp).toBeGreaterThan(rs)
    expect(rs).toBeGreaterThan(re)
  })

  it('选中节点放大 1.4 倍，优先于展开放大', () => {
    const pos = position('p1', 0)
    const base = nodeRadius(pos, false, false)
    expect(nodeRadius(pos, true, false)).toBeCloseTo(base * 1.4)
    // 同时选中且展开 → 仍按选中放大（不叠加）
    expect(nodeRadius(pos, true, true)).toBeCloseTo(base * 1.4)
  })

  it('展开岗位放大 1.2 倍，技能节点展开标记不放大', () => {
    const pos = position('p1', 0)
    const sk = skill('s1', 0)
    expect(nodeRadius(pos, false, true)).toBeCloseTo(nodeRadius(pos, false, false) * 1.2)
    // expanded 标记仅作用于岗位（技能节点即使标记展开也不放大）
    expect(nodeRadius(sk, false, true)).toBeCloseTo(nodeRadius(sk, false, false))
  })

  it('value 越大半径越大', () => {
    const small = skill('s1', 0)
    const big = skill('s2', 100)
    expect(nodeRadius(big, false, false)).toBeGreaterThan(nodeRadius(small, false, false))
  })
})

describe('computeFilterMarks', () => {
  const edges: GraphEdge[] = [
    { source: 'p1', target: 's1', necessity: 'must', weight: 0.8 },
    { source: 'p1', target: 's2', necessity: 'nice', weight: 0.4 },
    { source: 'p2', target: 's3', necessity: 'must', weight: 0.8 },
  ]

  it('无过滤时全部可见（打标为空）', () => {
    const nodes = [position('p1', 50), skill('s1', 10)]
    const marks = computeFilterMarks(nodes, edges, {
      minWeight: 0,
      hiddenStatuses: new Set(),
      showOnlyMustEdges: false,
    })
    expect(marks.dimNodeIds.size).toBe(0)
    expect(marks.dimEdgeFlags).toEqual([false, false, false])
    expect(marks.visibleNodes).toBe(2)
    expect(marks.visibleEdges).toBe(3)
  })

  it('权重低于阈值的节点被压暗（打标而非剔除，保持布局稳定）', () => {
    const nodes = [position('p1', 50), position('p2', 5), skill('s1', 30), skill('s3', 2)]
    const marks = computeFilterMarks(nodes, [], { minWeight: 10, hiddenStatuses: new Set(), showOnlyMustEdges: false })
    expect(marks.dimNodeIds.has('p2')).toBe(true)
    expect(marks.dimNodeIds.has('s3')).toBe(true)
    expect(marks.dimNodeIds.has('p1')).toBe(false)
    expect(marks.visibleNodes).toBe(2)
  })

  it('被隐藏状态的岗位压暗；技能节点不受状态过滤影响', () => {
    const nodes = [
      { id: 'p1', name: 'p1', type: 'position' as const, value: 50, status: 'archived' as PositionStatus },
      { id: 'p2', name: 'p2', type: 'position' as const, value: 50, status: 'stable' as PositionStatus },
      skill('s1', 50),
    ]
    const marks = computeFilterMarks(nodes, [], {
      minWeight: 0,
      hiddenStatuses: new Set<PositionStatus>(['archived']),
      showOnlyMustEdges: false,
    })
    expect(marks.dimNodeIds.has('p1')).toBe(true)
    expect(marks.dimNodeIds.has('p2')).toBe(false)
    expect(marks.dimNodeIds.has('s1')).toBe(false)
  })

  it('端点被压暗的边一并压暗（按 edges 同序标记）', () => {
    const nodes = [position('p1', 50), position('p2', 1), skill('s1', 30), skill('s3', 2)]
    const localEdges: GraphEdge[] = [
      { source: 'p1', target: 's1', necessity: 'must' },
      { source: 'p2', target: 's3', necessity: 'must' },
    ]
    // p2(1)、s3(2) 低于阈值压暗 → 其边压暗；p1-s1 两端可见保留
    const marks = computeFilterMarks(nodes, localEdges, { minWeight: 10, hiddenStatuses: new Set(), showOnlyMustEdges: false })
    expect(marks.dimEdgeFlags).toEqual([false, true])
    expect(marks.visibleEdges).toBe(1)
  })

  it('仅看必备关系时 nice 边压暗（must 边保留）', () => {
    const nodes = [position('p1', 50), position('p2', 50), skill('s1'), skill('s2'), skill('s3')]
    const marks = computeFilterMarks(nodes, edges, { minWeight: 0, hiddenStatuses: new Set(), showOnlyMustEdges: true })
    expect(marks.dimEdgeFlags).toEqual([false, true, false])
    expect(marks.visibleEdges).toBe(2)
  })

  it('value 缺失的节点按 0 参与权重过滤', () => {
    const marks = computeFilterMarks([skill('s1')], [], { minWeight: 1, hiddenStatuses: new Set(), showOnlyMustEdges: false })
    expect(marks.dimNodeIds.has('s1')).toBe(true)
  })
})

describe('hasPositionOverlap', () => {
  it('岗位间距 < 半径和 + minGap（-1px 容差）→ true', () => {
    // symbolSize 20 → 半径 10；minGap 60 → 判定阈 10+10+60-1 = 79
    const chart = makeChart(
      [{ type: 'position', symbolSize: 20 }, { type: 'position', symbolSize: 20 }],
      [[0, 0], [50, 0]],
    )
    expect(hasPositionOverlap(chart as never)).toBe(true)
  })

  it('间距足够（≥ 半径和 + minGap）→ false', () => {
    const chart = makeChart(
      [{ type: 'position', symbolSize: 20 }, { type: 'position', symbolSize: 20 }],
      [[0, 0], [100, 0]],
    )
    expect(hasPositionOverlap(chart as never)).toBe(false)
  })

  it('技能节点重叠不计（仅统计岗位节点）', () => {
    const chart = makeChart(
      [{ type: 'position', symbolSize: 20 }, { type: 'skill', symbolSize: 20 }],
      [[0, 0], [0, 0]],
    )
    expect(hasPositionOverlap(chart as never)).toBe(false)
  })

  it('chart 已 dispose（getModel 返回 null）→ false 不抛错', () => {
    const chart = makeChart([], [])
    chart.getModel = () => null
    expect(hasPositionOverlap(chart as never)).toBe(false)
  })
})

describe('enforceSpread', () => {
  it('重叠岗位对沿连线双向推开至 minDist（默认硬写回）', () => {
    // 半径 10 + minGap 14 → minDist 34；间距 20 → 各推 7 → 34
    const chart = makeChart(
      [{ type: 'position', symbolSize: 20 }, { type: 'position', symbolSize: 20 }],
      [[0, 0], [20, 0]],
    )
    enforceSpread(chart as never, { minGap: 14 })
    expect(chart.layouts[0]).toEqual([-7, 0])
    expect(chart.layouts[1]).toEqual([27, 0])
    expect(chart.refreshed()).toBeGreaterThan(0)
  })

  it('无重叠不位移', () => {
    const chart = makeChart(
      [{ type: 'position', symbolSize: 20 }, { type: 'position', symbolSize: 20 }],
      [[0, 0], [100, 0]],
    )
    enforceSpread(chart as never, { minGap: 14 })
    expect(chart.layouts[0]).toEqual([0, 0])
    expect(chart.layouts[1]).toEqual([100, 0])
  })

  it('重叠对经 maxIterations 多轮迭代收敛（三角连环重叠）', () => {
    // 3 个岗位两两重叠（间距 12/12/16.97 均 < 34）：松弛迭代后任意两两 ≥ minDist
    const chart = makeChart(
      [{ type: 'position', symbolSize: 20 }, { type: 'position', symbolSize: 20 }, { type: 'position', symbolSize: 20 }],
      [[0, 0], [12, 0], [0, 12]],
    )
    enforceSpread(chart as never, { minGap: 14, maxIterations: 20 })
    const pts = chart.layouts.map((l) => (l as number[]).slice(0, 2))
    for (let i = 0; i < pts.length; i++) {
      for (let j = i + 1; j < pts.length; j++) {
        const d = Math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
        expect(d).toBeGreaterThanOrEqual(34 - 1e-6)
      }
    }
  })

  it('animate 模式经 rAF 插值到终态（无硬跳）', () => {
    vi.useFakeTimers()
    try {
      const chart = makeChart(
        [{ type: 'position', symbolSize: 20 }, { type: 'position', symbolSize: 20 }],
        [[0, 0], [20, 0]],
      )
      enforceSpread(chart as never, { minGap: 14, animate: true, duration: 100 })
      // 首帧同步执行后注册 rAF；推进超过 duration → 插值到终态（= 硬写回位置）
      vi.advanceTimersByTime(150)
      expect(chart.layouts[0]).toEqual([-7, 0])
      expect(chart.layouts[1]).toEqual([27, 0])
    } finally {
      vi.useRealTimers()
    }
  })
})
