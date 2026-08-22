import type { GraphEdge, GraphNode, PositionStatus } from './types'

/** 稠密图判定阈值：技能数超过该值时 band1 标签改用 75 分位（视觉评审 P0-3） */
const DENSE_SKILL_COUNT = 60

/** 技能标签显示阈值：低于该 value 的技能不常显标签（悬停/选中时经 emphasis 仍显示），
 *  避免技能全量渲染时标签叠字遮挡，同时减少 label 渲染开销。
 *  稀疏图（≤60 技能）取中位数（上中位）；稠密图（>60，技术栈视图 100+ 技能同画布）
 *  取 75 分位——band1 只亮头部分位技能标签，缓解中心毛发球标签压盖（08-22 视觉评审） */
export function skillLabelThreshold(nodes: GraphNode[]): number {
  const values = nodes
    .filter((n) => n.type === 'skill')
    .map((n) => n.value ?? 0)
    .sort((a, b) => a - b)
  if (values.length === 0) return 0
  const q = values.length > DENSE_SKILL_COUNT ? 0.75 : 0.5
  return values[Math.min(values.length - 1, Math.floor(values.length * q))]
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

/** 过滤面板三控件的状态快照 */
export interface FilterOptions {
  minWeight: number
  /** 被隐藏的岗位状态集合（空集 = 全显示） */
  hiddenStatuses: Set<PositionStatus>
  /** true = 仅看 must（必备）边 */
  showOnlyMustEdges: boolean
}

/** 过滤打标结果：节点/边不从图中剔除，仅标记压暗 */
export interface FilterMarks {
  /** 被压暗的节点 id 集合 */
  dimNodeIds: Set<string>
  /** 与 edges 同序的压暗标记 */
  dimEdgeFlags: boolean[]
  visibleNodes: number
  visibleEdges: number
}

/**
 * 计算图谱过滤打标（纯函数）：权重低于阈值、或岗位状态被隐藏的节点压暗；
 * 端点被压暗的边、"仅看必备关系"下的非 must 边一并压暗。
 *
 * 打标而非剔除——ECharts 力导向在节点集合与顺序不变时保留既有布局坐标，
 * 筛选只改透明度不触发全图重新收敛，避免布局跳变（演示时镜头稳定）。
 */
export function computeFilterMarks(
  nodes: GraphNode[],
  edges: GraphEdge[],
  opts: FilterOptions,
): FilterMarks {
  const dimNodeIds = new Set<string>()
  for (const n of nodes) {
    if ((n.value ?? 0) < opts.minWeight) {
      dimNodeIds.add(n.id)
      continue
    }
    if (n.type === 'position' && n.status && opts.hiddenStatuses.has(n.status)) {
      dimNodeIds.add(n.id)
    }
  }
  let visibleEdges = 0
  const dimEdgeFlags = edges.map((e) => {
    const dim =
      dimNodeIds.has(e.source) ||
      dimNodeIds.has(e.target) ||
      (opts.showOnlyMustEdges && e.necessity !== 'must')
    if (!dim) visibleEdges += 1
    return dim
  })
  return {
    dimNodeIds,
    dimEdgeFlags,
    visibleNodes: nodes.length - dimNodeIds.size,
    visibleEdges,
  }
}

