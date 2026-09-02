/**
 * 岗位域内二级子分组纯函数测试。
 * 覆盖：专属词主组（大模型/CV/机器人/芯片/Web前端）、多命中取主组+兼组、
 * 未命中归「通用/其他」兜底、输出子组顺序稳定。
 */
import { describe, it, expect } from 'vitest'
import type { GraphEdge, GraphNode } from './types'
import {
  assignPositionToSubgroup,
  groupPositionsBySubgroup,
  FALLBACK_SUBGROUP,
  SUBGROUPS,
} from './graph-subgroup'

function node(id: string, name: string, type: GraphNode['type'] = 'skill'): GraphNode {
  return { id, name, type } as GraphNode
}

function edge(source: string, target: string, weight = 0.8): GraphEdge {
  return { source, target, weight } as GraphEdge
}

// 构造：岗位 pos_X 连接若干技能（skill id → name），权重降序表示核心度
function edgesFor(pos: string, skills: [string, number][], allSkills: GraphNode[]): GraphEdge[] {
  const byId = new Map(allSkills.map((s) => [s.id, s]))
  return skills.map(([id, w]) => (byId.get(id) ? edge(pos, id, w) : null)).filter(Boolean) as GraphEdge[]
}

describe('assignPositionToSubgroup', () => {
  const skills: GraphNode[] = [
    node('sk1', '大语言模型'), node('sk2', '检索增强生成'), node('sk3', '计算机视觉'),
    node('sk4', '目标检测'), node('sk5', 'ROS'), node('sk6', '运动控制'), node('sk7', 'FPGA'),
    node('sk8', 'SystemVerilog'), node('sk9', 'Vue.js'), node('sk10', 'React'),
    node('sk11', 'PyTorch'), node('sk12', '数据结构'), node('sk13', '推荐算法'),
    node('sk14', '语音识别'),
  ]
  const skillMap = new Map(skills.map((s) => [s.id, s.name]))

  it('大模型岗归「大模型/LLM」主组，不因含 PyTorch 误归视觉', () => {
    const e = edgesFor('posA', [['sk1', 0.8], ['sk2', 0.8], ['sk11', 0.8]], skills)
    const a = assignPositionToSubgroup('posA', skillMap, e)
    expect(a.primary).toBe('大模型/LLM')
    expect(a.secondary).not.toContain('计算机视觉')
  })

  it('视觉岗归「计算机视觉」（OpenCV/目标检测专属词）', () => {
    const e = edgesFor('posV', [['sk3', 0.8], ['sk4', 0.8]], skills)
    expect(assignPositionToSubgroup('posV', skillMap, e).primary).toBe('计算机视觉')
  })

  it('机器人岗归「机器人/自动驾驶」（ROS/运动控制）', () => {
    const e = edgesFor('posR', [['sk5', 0.8], ['sk6', 0.8]], skills)
    expect(assignPositionToSubgroup('posR', skillMap, e).primary).toBe('机器人/自动驾驶')
  })

  it('芯片岗归「芯片/嵌入/验证」（FPGA/SystemVerilog）', () => {
    const e = edgesFor('posC', [['sk7', 0.8], ['sk8', 0.8]], skills)
    expect(assignPositionToSubgroup('posC', skillMap, e).primary).toBe('芯片/嵌入/验证')
  })

  it('全栈岗多命中：Web前端主、含后端/数据库兼（非误报，真实多面性）', () => {
    const e = edgesFor('posFS', [['sk9', 0.8], ['sk10', 0.8], ['sk11', 0.4]], skills)
    const a = assignPositionToSubgroup('posFS', skillMap, e)
    expect(a.primary).toBe('Web前端')
    // 只含 vue/react 命中 Web前端，无后端专属词，故 secondary 空
    expect(a.secondary).toEqual([])
  })

  it('通用算法岗（仅跨组基础词 PyTorch/数据结构）未命中 → 落「通用/其他」', () => {
    const e = edgesFor('posG', [['sk11', 0.8], ['sk12', 0.4]], skills)
    expect(assignPositionToSubgroup('posG', skillMap, e).primary).toBe(FALLBACK_SUBGROUP)
  })

  it('推荐岗（推荐算法）归「推荐/搜索」', () => {
    const e = edgesFor('posRec', [['sk13', 0.8]], skills)
    expect(assignPositionToSubgroup('posRec', skillMap, e).primary).toBe('推荐/搜索')
  })
})

describe('groupPositionsBySubgroup', () => {
  it('按子组顺序输出，空组剔除，未命中岗位落兜底组', () => {
    const skills: GraphNode[] = [
      node('sk1', '大语言模型'), node('sk2', '计算机视觉'), node('sk3', 'FPGA'),
      node('sk11', 'PyTorch'), node('sk12', '数据结构'),
    ]
    const pos = [
      node('p1', '大模型算法工程师', 'position'),
      node('p2', '视觉算法工程师', 'position'),
      node('p3', '通用算法工程师', 'position'),
    ]
    const edges: GraphEdge[] = [
      edge('p1', 'sk1', 0.8),
      edge('p2', 'sk2', 0.8),
      edge('p3', 'sk11', 0.8), edge('p3', 'sk12', 0.4),
    ]
    const groups = groupPositionsBySubgroup(pos, edges, skills)
    const labels = groups.map((g) => g.label)
    // 大模型、计算机视觉、芯片（空）、通用/其他（兜底）
    const order = [...SUBGROUPS.map((s) => s.label), FALLBACK_SUBGROUP]
    // 输出顺序 = order 中非空子组的相对顺序（大模型<计算机视觉<通用/其他，芯片空被剔除）
    expect(labels[0]).toBe('大模型/LLM')
    expect(labels).toContain('计算机视觉')
    expect(labels).toContain(FALLBACK_SUBGROUP)
    expect(labels).not.toContain('芯片/嵌入/验证') // 空组剔除
    // 每个组内岗位都在
    const llm = groups.find((g) => g.label === '大模型/LLM')!
    expect(llm.positions.map((p) => p.name)).toContain('大模型算法工程师')
    const fb = groups.find((g) => g.label === FALLBACK_SUBGROUP)!
    expect(fb.positions.map((p) => p.name)).toContain('通用算法工程师')
    // 稳定：order 索引单调递增
    const idx = labels.map((l) => order.indexOf(l))
    expect([...idx].sort((a, b) => a - b)).toEqual(idx)
  })
})
