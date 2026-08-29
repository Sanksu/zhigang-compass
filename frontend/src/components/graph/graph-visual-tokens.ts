import type { PositionStatus } from './types'

export type GraphTheme = 'light' | 'dark'

export const GRAPH_STATUS_ORDER = [
  'active',
  'stable',
  'emerging',
  'candidate',
  'declining',
  'archived',
] as const satisfies readonly PositionStatus[]

export const GRAPH_STATUS_META: Record<PositionStatus, { label: string; color: string }> = {
  active: { label: '活跃', color: '#64748B' },
  stable: { label: '稳定', color: '#3B82F6' },
  emerging: { label: '新兴', color: '#10B981' },
  candidate: { label: '候选', color: '#71717A' },
  declining: { label: '衰退', color: '#F59E0B' },
  archived: { label: '归档', color: '#EF4444' },
}

export const GRAPH_COLOR_BY_STATUS: Record<PositionStatus, string> = Object.fromEntries(
  Object.entries(GRAPH_STATUS_META).map(([status, meta]) => [status, meta.color]),
) as Record<PositionStatus, string>

const THEME_COLORS = {
  light: {
    canvas: '#F4F7FA', ink: '#172033', muted: '#66758A', border: '#DCE5EF', borderStrong: '#B8C7D8',
    domain: '#4F46E5', skill: '#172033', softSkill: '#D9468D', evidence: '#8B98A9', attr: '#7C3AED',
    edge: '#B8C7D8', edgeStrong: '#315C8C', edgeOptional: '#6D87A5',
    tooltip: '#FFFFFF', tooltipBorder: '#DCE5EF', labelSurface: 'rgba(244,247,250,0.92)', selectionRing: '#315C8C',
  },
  dark: {
    canvas: '#0B1524', ink: '#E7EEF7', muted: '#9AA9BC', border: '#1D3045', borderStrong: '#40556C',
    domain: '#8B8AF8', skill: '#E7EEF7', softSkill: '#F58FBC', evidence: '#718198', attr: '#A78BFA',
    edge: '#40556C', edgeStrong: '#78A5D6', edgeOptional: '#91A9C5',
    tooltip: '#0F1C2D', tooltipBorder: '#40556C', labelSurface: 'rgba(11,21,36,0.92)', selectionRing: '#78A5D6',
  },
} as const

export const GRAPH_OPACITY = { node: 0.95, edge: { light: 0.2, dark: 0.26 }, filterNode: 0.08, filterEdge: 0.04, context: 0.1, focus: 0.9 } as const

export function graphColors(theme: GraphTheme) {
  return THEME_COLORS[theme]
}

export function graphNodeColor(theme: GraphTheme, kind: 'domain' | 'skill' | 'softSkill' | 'evidence' | 'attr' | 'position', status: PositionStatus = 'candidate'): string {
  if (kind === 'position') return GRAPH_COLOR_BY_STATUS[status]
  return THEME_COLORS[theme][kind]
}

/* 技能类目着色（08-28 技术栈视图降噪）：按图谱 skill_category 归入大类配色，
   画布 colorOf 与图例共用本表。匹配按 contains 顺序取首个命中，未命中回落
   默认技能色（graphNodeColor(theme, 'skill')，由调用方兜底）。 */
export interface SkillCategoryStyle {
  /** 命中关键词（对 skill_category 做 contains 匹配，小写比较） */
  match: string[]
  color: string
  label: string
}

export const SKILL_CATEGORY_PALETTE: SkillCategoryStyle[] = [
  { match: ['语言'], color: '#6366f1', label: '编程语言' },
  { match: ['ai', '机器学习', '算法', '大模型', 'llm', 'nlp', '语音', '视觉'], color: '#8b5cf6', label: 'AI/算法' },
  { match: ['前端', 'web'], color: '#0ea5e9', label: '前端' },
  { match: ['后端', '架构', '分布式', '微服务', '中间件', '消息'], color: '#3b82f6', label: '后端/架构' },
  { match: ['数据库', '缓存', 'sql'], color: '#14b8a6', label: '数据库/存储' },
  { match: ['大数据', '数据工程', '数仓', 'etl'], color: '#06b6d4', label: '大数据' },
  { match: ['云', 'devops', '运维', 'sre', '容器', 'k8s'], color: '#f59e0b', label: '云/DevOps' },
  { match: ['安全'], color: '#ef4444', label: '安全' },
  { match: ['测试', '质量'], color: '#84cc16', label: '测试' },
  { match: ['移动', '客户端', 'android', 'ios', '桌面'], color: '#ec4899', label: '移动/客户端' },
  { match: ['网络', '协议'], color: '#64748b', label: '网络/协议' },
  { match: ['游戏', '引擎', '图形'], color: '#a855f7', label: '游戏/图形' },
  { match: ['硬件', '芯片', '嵌入式'], color: '#78716c', label: '硬件/嵌入式' },
]

export function skillCategoryColor(category: string | null | undefined): string | null {
  const c = (category ?? '').trim().toLowerCase()
  if (!c) return null
  for (const item of SKILL_CATEGORY_PALETTE) {
    if (item.match.some((kw) => c.includes(kw))) return item.color
  }
  return null
}

/* 职能域社区着色（08-29 图谱优化提案③落地）：域超节点按 domain_id 稳定哈希
   从 12 色社区色板取色——同域恒同色（跨会话/跨刷新一致），相邻哈希色相岔开，
   15+ 域在全景图上一眼区分聚团。色板与技能类目 13 色、岗位状态 6 色错位；
   待归类桶不走本表（保持中性灰弱化语义，见 graph-2d colorOf）。 */
const DOMAIN_COMMUNITY_PALETTE: Record<GraphTheme, readonly string[]> = {
  // 浅色主题：中深色调，浅底上可读且不与墨色技能节点混淆
  light: [
    '#4F46E5', '#0369A1', '#047857', '#B45309', '#BE185D', '#7C3AED',
    '#0F766E', '#9F1239', '#1D4ED8', '#C2410C', '#15803D', '#5B21B6',
  ],
  // 暗色主题：提亮一档的同相色，深底上保持饱和可辨
  dark: [
    '#8B8AF8', '#38BDF8', '#34D399', '#FBBF24', '#F472B6', '#A78BFA',
    '#2DD4BF', '#FB7185', '#60A5FA', '#FDBA74', '#4ADE80', '#C4B5FD',
  ],
}

/** FNV-1a 32 位哈希：域 id → 色板下标（稳定，无外链依赖） */
function domainHash(seed: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h
}

export function domainCommunityColor(seed: string, theme: GraphTheme): string {
  const palette = DOMAIN_COMMUNITY_PALETTE[theme]
  return palette[domainHash(seed) % palette.length]
}
