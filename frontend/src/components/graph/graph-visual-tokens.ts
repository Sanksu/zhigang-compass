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
    domain: '#4F46E5', skill: '#172033', softSkill: '#D9468D', evidence: '#8B98A9',
    edge: '#B8C7D8', edgeStrong: '#315C8C', edgeOptional: '#6D87A5',
    tooltip: '#FFFFFF', tooltipBorder: '#DCE5EF', labelSurface: 'rgba(244,247,250,0.66)', selectionRing: '#315C8C',
  },
  dark: {
    canvas: '#0B1524', ink: '#E7EEF7', muted: '#9AA9BC', border: '#1D3045', borderStrong: '#40556C',
    domain: '#8B8AF8', skill: '#E7EEF7', softSkill: '#F58FBC', evidence: '#718198',
    edge: '#40556C', edgeStrong: '#78A5D6', edgeOptional: '#91A9C5',
    tooltip: '#0F1C2D', tooltipBorder: '#40556C', labelSurface: 'rgba(11,21,36,0.66)', selectionRing: '#78A5D6',
  },
} as const

export const GRAPH_OPACITY = { node: 0.95, edge: { light: 0.2, dark: 0.26 }, filterNode: 0.08, filterEdge: 0.04, context: 0.1, focus: 0.9 } as const

export function graphColors(theme: GraphTheme) {
  return THEME_COLORS[theme]
}

export function graphNodeColor(theme: GraphTheme, kind: 'domain' | 'skill' | 'softSkill' | 'evidence' | 'position', status: PositionStatus = 'candidate'): string {
  if (kind === 'position') return GRAPH_COLOR_BY_STATUS[status]
  return THEME_COLORS[theme][kind]
}
