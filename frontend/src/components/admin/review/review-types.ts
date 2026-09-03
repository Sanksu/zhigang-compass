/**
 * 岗位审核页共享类型与置信度工具（管理域拆分）
 *
 * 契约生成类型别名 + 小型纯函数。所有类型均来自 src/types/api.d.ts
 * （openapi-typescript 从 backend/openapi/openapi.yaml 生成）；契约字段变更时
 * 重新生成 api.d.ts 即可，本文件不做结构定义。此处仅放多 Tab 共用的别名与
 * 纯函数，各 Tab 私有类型（SkillFormRow / WatchRow 等）留在各自组件内。
 */
import type { components } from '@/types/api'

export type Schema = components['schemas']

/** 后端 /admin/positions/pending 候选池项（契约 DiscoveryCandidateItem，AL-M4-01） */
export type ReviewItem = Schema['DiscoveryCandidateItem']

/** 后端 /admin/evolution/pending 项（emerging 待演化审核，契约 DiscoveryCandidateItem） */
export type EvolutionItem = Schema['DiscoveryCandidateItem']

/** 后端 /admin/positions/declining 项（declining 待归档，契约 DiscoveryCandidateItem） */
export type DecliningItem = Schema['DiscoveryCandidateItem']

/** 后端 /admin/positions/stable 并集项（契约 StablePositionItem：候选池 ∪ 图谱留存） */
export type StableItem = Schema['StablePositionItem']

/** 综合置信度（后端 confidence 为多维对象，取 final_confidence 或 0.5 中性值） */
export function confidenceOf(item: ReviewItem): number {
  const c = item.confidence
  if (c && typeof c === 'object') {
    const score = (c as Record<string, unknown>).final_confidence
    if (typeof score === 'number') return score
  }
  return 0.5
}

/**
 * stable 并集项置信度（GET /admin/positions/stable）：
 * 仅 pool 来源有 confidence（graph 来源为 null），无则返回 null（前端显示 —）。
 */
export function stableConfidenceOf(item: StableItem): number | null {
  const c = item.confidence
  if (c && typeof c === 'object') {
    const score = (c as Record<string, unknown>).final_confidence
    if (typeof score === 'number') return score
  }
  return null
}

/**
 * P1 置信度标量化阻断复核阈值：final_confidence < 0.75 的候选标记"需复核"。
 * 与后端 confidence.py REVIEW_BLOCK_THRESHOLD 同步（证据距离优先：低证据候选
 * 自动进入审核队列阻断，人工复核后方可晋升）。
 */
export const REVIEW_BLOCK_THRESHOLD = 0.75

/** 是否需人工复核（低置信度候选自动标记阻断） */
export function needsReview(item: ReviewItem): boolean {
  return confidenceOf(item) < REVIEW_BLOCK_THRESHOLD
}

/** 证据距离综合分（P1：图谱证据距离×0.5 + LLM Logprob×0.5，缺省中性 0.5） */
export function evidenceScoreOf(item: ReviewItem): number {
  const c = item.confidence
  if (c && typeof c === 'object') {
    const score = (c as Record<string, unknown>).evidence_score
    if (typeof score === 'number') return score
  }
  return 0.5
}

export const CONFIDENCE_TONE = (c: number) =>
  c >= 0.8 ? 'text-state-emerging' : c >= 0.7 ? 'text-state-stable' : 'text-state-declining'
