import type { GraphNode, PositionStatus } from './types'

/** 技能标签显示阈值：低于全图技能节点 value 中位数的不常显标签（悬停/选中时经 emphasis 仍显示），
 *  避免技能全量渲染时标签叠字遮挡，同时减少 label 渲染开销 */
export function skillLabelThreshold(nodes: GraphNode[]): number {
  const values = nodes
    .filter((n) => n.type === 'skill')
    .map((n) => n.value ?? 0)
    .sort((a, b) => a - b)
  if (values.length === 0) return 0
  return values[Math.floor(values.length / 2)]
}

/** 节点视觉半径：岗位 > 技能 > 证据；展开的岗位与选中节点放大（3D 画布使用） */
export function nodeRadius(node: GraphNode, selected: boolean, expanded: boolean): number {
  const v = node.value ?? 30
  const base = node.type === 'position' ? 5 : node.type === 'skill' ? 2.6 : 1.8
  let r = base + (v / 100) * 2.4
  if (selected) r *= 1.4
  else if (expanded && node.type === 'position') r *= 1.2
  return r
}

/** 岗位状态机 → 颜色（与 globals.css 中状态色对齐，设计令牌单一事实源；2D/3D 共用） */
export const COLOR_BY_STATUS: Record<PositionStatus, string> = {
  active: '#64748b',
  candidate: '#71717a',
  emerging: '#10b981',
  stable: '#3b82f6',
  declining: '#f59e0b',
  archived: '#ef4444',
}

