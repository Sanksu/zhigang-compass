import type { GraphEdge, GraphNode, PositionStatus } from './types'

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

/** 软技能类目值（与后端 skill_whitelist.yaml 的 category 命名一致） */
export const SOFT_SKILL_CATEGORY = '软技能'

/** 软技能配色（粉色系，与六态状态色/域紫/技能黑白均区分；2D/3D 共用） */
export const COLOR_SOFT_LIGHT = '#ec4899'
export const COLOR_SOFT_DARK = '#f472b6'

/** 软技能判定：skill 节点且类目为「软技能」（责任心/沟通能力等软素质，与技术栈技能区分展示） */
export function isSoftSkill(node: GraphNode): boolean {
  return node.type === 'skill' && node.skill_category === SOFT_SKILL_CATEGORY
}

/** 过滤面板的状态快照 */
export interface FilterOptions {
  minWeight: number
  /** 被隐藏的岗位状态集合（空集 = 全显示） */
  hiddenStatuses: Set<PositionStatus>
  /** true = 仅看 must（必备）边 */
  showOnlyMustEdges: boolean
  /** true = 压暗软技能节点（与技术栈技能分开查看） */
  hideSoftSkills?: boolean
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
 * 计算图谱过滤打标（纯函数）：权重低于阈值、岗位状态被隐藏、或软技能被
 * 隐藏的节点压暗；端点被压暗的边、"仅看必备关系"下的非 must 边一并压暗。
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
      continue
    }
    if (opts.hideSoftSkills && isSoftSkill(n)) {
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

