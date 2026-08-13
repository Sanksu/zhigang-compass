/**
 * 图谱展示层类型 — 单一事实源：backend/openapi/openapi.yaml
 * （GraphNode/GraphEdge/GraphViewType/GraphViewData schema），经
 * openapi-typescript 生成至 src/types/api.d.ts（npm run gen:api）。
 *
 * AGENTS.md 铁律一：前端不得直接定义与后端不一致的类型。本文件仅
 * 承载两类内容：
 * 1. 契约类型的再导出（GraphNode/GraphEdge/GraphViewType/PositionStatus）
 * 2. 前端自算展示字段的派生（度数 value——toGraphData 由 edges 统计，
 *    非后端返回）
 *
 * NodeDetail/GraphData/GraphStats 为前端组件状态/统计容器（非 API 响应
 * 形状），元素类型一律取自契约。
 */

import type { components } from '@/types/api'

/** 契约 GraphNode.type（position/skill/evidence） */
export type NodeType = NonNullable<components['schemas']['GraphNode']['type']>
/** 契约 GraphNode.status（岗位五状态机） */
export type PositionStatus = NonNullable<components['schemas']['GraphNode']['status']>
/** 契约 GraphViewType（四种视图枚举） */
export type GraphViewType = components['schemas']['GraphViewType']

/** 契约 GraphNode + 前端自算度数 value（布局权重，非后端返回字段） */
export type GraphNode = components['schemas']['GraphNode'] & {
  /** 节点度数（toGraphData 由 edges 统计，驱动布局斥力/大小） */
  value?: number
}

/** 契约 GraphEdge（source/target/weight/necessity/level） */
export type GraphEdge = components['schemas']['GraphEdge']

/** 前端图统计容器（toGraphData 由返回 nodes/edges 自算，非后端字段） */
export interface GraphStats {
  totalPositions: number
  totalSkills: number
  totalEdges: number
  /** 实际返回节点数（受 limit 约束） */
  returnedNodes: number
  /** 全量节点总数（可能 > returnedNodes） */
  totalNodesInGraph: number
}

/** 前端图数据容器（由契约 GraphViewData 映射而来） */
export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats: GraphStats
}

/** 节点详情面板所需的最小信息（前端组件状态，非 API 类型） */
export interface NodeDetail {
  id: string
  name: string
  type: NodeType
  status?: PositionStatus
  /** 技能级别（后端 view 端点未返回，面板预留展示） */
  level?: string
  /** 证据来源（后端 view 端点未返回，面板预留展示） */
  source?: string
  value?: number
  description?: string
}
