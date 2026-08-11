/**
 * 图谱渲染纯函数单测 — 覆盖本轮显示逻辑优化新增的核心行为：
 * - graph-2d：技能标签密度阈值（中位数截断，低关联技能不常显）
 * - graph-3d：节点视觉半径（类型基础差 + 选中/展开放大）
 */
import { describe, expect, it } from 'vitest'
import { skillLabelThreshold, nodeRadius } from './graph-utils'
import type { GraphNode } from './types'

function skill(id: string, value?: number): GraphNode {
  return { id, name: id, type: 'skill', value }
}

function position(id: string, value?: number): GraphNode {
  return { id, name: id, type: 'position', value }
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
