import type { components } from '@/types/api'
import type { GraphData, GraphEdge, GraphNode } from './types'

type GraphViewData = components['schemas']['GraphViewData']

const POSITION_STATUSES: GraphNode['status'][] = [
  'active',
  'candidate',
  'emerging',
  'stable',
  'declining',
  'archived',
]

function isValidStatus(status?: string): status is NonNullable<GraphNode['status']> {
  return !!status && POSITION_STATUSES.includes(status as NonNullable<GraphNode['status']>)
}

export function toGraphData(raw: GraphViewData): GraphData {
  const degree = new Map<string, number>()
  raw.edges.forEach((edge) => {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1)
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1)
  })

  const nodes: GraphNode[] = raw.nodes.map((node) => ({
    id: node.id,
    name: node.name,
    type: node.type,
    value: degree.get(node.id) ?? 0,
    // 岗位画像大类标记透传（positionPortrait 视图层级布局的分流依据）
    ...(node.type === 'attr' && node.portrait_category ? { portrait_category: true } : {}),
    status: node.type === 'position' ? (isValidStatus(node.status) ? node.status : 'candidate') : undefined,
    ...(node.type === 'position'
      ? { domain_id: node.domain_id ?? undefined, domain_name: node.domain_name ?? undefined }
      : {
          skill_category: node.skill_category ?? undefined,
          // 岗位画像技能：透传 description（技能说明）与 jd_source_count（JD 支撑数）
          description: node.description ?? undefined,
          jd_source_count: node.jd_source_count ?? undefined,
        }),
  }))
  const edges: GraphEdge[] = raw.edges.map((edge) => ({
    source: edge.source,
    target: edge.target,
    necessity: edge.necessity === 'nice' ? 'nice' : 'must',
    weight: edge.weight,
    // 熟练度级别（REQUIRES.level）：级别筛选视图的数据依据，透传给详情面板
    level: edge.level ?? undefined,
  }))

  return {
    nodes,
    edges,
    stats: {
      totalPositions: nodes.filter((node) => node.type === 'position').length,
      totalSkills: nodes.filter((node) => node.type === 'skill').length,
      totalEdges: edges.length,
      returnedNodes: nodes.length,
      totalNodesInGraph: raw.stats?.total_nodes ?? nodes.length,
    },
  }
}
