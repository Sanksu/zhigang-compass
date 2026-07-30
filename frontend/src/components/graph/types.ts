/**
 * 图谱数据类型 — 设计文档 §10.3 + openapi.yaml /api/v1/graph/panorama
 *
 * 节点三类：position（岗位）/ skill（技能）/ evidence（证据）
 * 岗位五状态机：candidate/emerging/stable/declining/archived
 */

export type NodeType = 'position' | 'skill' | 'evidence'

export type PositionStatus =
  | 'candidate'
  | 'emerging'
  | 'stable'
  | 'declining'
  | 'archived'

export type GraphViewType = 'panorama' | 'techStack' | 'level' | 'positionCenter'

export interface GraphNode {
  id: string
  name: string
  /** 节点类型，决定形状与基础色 */
  type: NodeType
  /** 节点权重（度数或后端计算的 score），影响节点大小 */
  value?: number
  /** 岗位状态机（仅 position 节点有效） */
  status?: PositionStatus
  /** 技能级别（仅 skill 节点有效，初级/中级/高级/专家） */
  level?: string
  /** 证据来源（仅 evidence 节点有效） */
  source?: string
  /** 额外描述 */
  description?: string
}

export interface GraphEdge {
  source: string
  target: string
  /** 关系类型：岗位-技能 / 技能-证据 */
  relation?: 'requires' | 'proves'
  /** 必要性（仅 requires 关系） */
  necessity?: 'must' | 'nice'
  /** 边权重（0-1），影响粗细 */
  weight?: number
}

export interface GraphStats {
  totalPositions: number
  totalSkills: number
  totalEdges: number
  /** 实际返回节点数（受 limit 约束） */
  returnedNodes: number
  /** 全量节点总数（可能 > returnedNodes） */
  totalNodesInGraph: number
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats: GraphStats
}

/** 节点详情面板所需的最小信息 */
export interface NodeDetail {
  id: string
  name: string
  type: NodeType
  status?: PositionStatus
  level?: string
  source?: string
  value?: number
  description?: string
}
