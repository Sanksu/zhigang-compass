/**
 * 节点详情面板 — 设计文档 §10.3
 *
 * 图谱画布右侧 25-30% 宽度展示选中节点的详细信息：
 * - position：状态机、关联技能数、description
 * - skill：级别、被多少岗位要求、关联证据
 * - evidence：来源、描述
 */
import { X, Network, Cpu, FileText } from 'lucide-react'
import type { NodeDetail, PositionStatus } from './types'
import { Badge } from '@/components/ui/badge'

interface NodeDetailPanelProps {
  node: NodeDetail | null
  /** 关联边统计（按类型计数） */
  stats?: {
    positionCount?: number
    skillCount?: number
    evidenceCount?: number
  }
  onClose?: () => void
}

const STATUS_LABEL: Record<PositionStatus, string> = {
  candidate: '候选',
  emerging: '新兴',
  stable: '稳定',
  declining: '衰退',
  archived: '归档',
}

const STATUS_CLASS: Record<PositionStatus, string> = {
  candidate: 'bg-state-candidate/15 text-state-candidate border-state-candidate/30',
  emerging: 'bg-state-emerging/15 text-state-emerging border-state-emerging/30',
  stable: 'bg-state-stable/15 text-state-stable border-state-stable/30',
  declining: 'bg-state-declining/15 text-state-declining border-state-declining/30',
  archived: 'bg-state-archived/15 text-state-archived border-state-archived/30',
}

const TYPE_LABEL: Record<NodeDetail['type'], string> = {
  position: '岗位',
  skill: '技能',
  evidence: '证据',
}

const TYPE_ICON: Record<NodeDetail['type'], typeof Network> = {
  position: Network,
  skill: Cpu,
  evidence: FileText,
}

export function NodeDetailPanel({ node, stats, onClose }: NodeDetailPanelProps) {
  if (!node) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-6 py-12 text-center">
        <Network className="size-8 text-ink-faint" />
        <p className="text-sm text-ink-muted">点击图谱节点查看详情</p>
        <p className="text-xs text-ink-faint max-w-[220px]">
          节点详情面板将展示岗位状态、技能级别与证据来源
        </p>
      </div>
    )
  }

  const Icon = TYPE_ICON[node.type]

  return (
    <div className="flex h-full flex-col">
      {/* 头部 */}
      <div className="flex items-start justify-between gap-3 border-b border-border p-4">
        <div className="flex items-start gap-3 min-w-0">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-subtle">
            <Icon className="size-4 text-ink-secondary" />
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-ink truncate">{node.name}</h3>
            <p className="text-xs text-ink-muted mt-0.5">{TYPE_LABEL[node.type]} · {node.id}</p>
          </div>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="rounded-sm p-1 text-ink-faint transition-colors hover:bg-subtle hover:text-ink"
            aria-label="关闭详情"
          >
            <X className="size-4" />
          </button>
        )}
      </div>

      {/* 内容 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* 类型与状态 */}
        <section className="space-y-2">
          <h4 className="text-xs font-medium text-ink-muted uppercase tracking-wide">属性</h4>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline" className="text-xs">
              {TYPE_LABEL[node.type]}
            </Badge>
            {node.type === 'position' && node.status && (
              <Badge variant="outline" className={`text-xs border ${STATUS_CLASS[node.status]}`}>
                {STATUS_LABEL[node.status]}
              </Badge>
            )}
            {node.type === 'skill' && node.level && (
              <Badge variant="outline" className="text-xs">{node.level}</Badge>
            )}
          </div>
        </section>

        {/* 权重 */}
        {typeof node.value === 'number' && (
          <section className="space-y-1.5">
            <h4 className="text-xs font-medium text-ink-muted uppercase tracking-wide">权重</h4>
            <div className="flex items-center gap-2">
              <div className="flex-1 h-1.5 rounded-full bg-subtle overflow-hidden">
                <div
                  className="h-full rounded-full bg-ink"
                  style={{ width: `${Math.min(100, node.value)}%` }}
                />
              </div>
              <span className="text-xs font-mono text-ink-secondary tabular-nums">{node.value}</span>
            </div>
          </section>
        )}

        {/* 来源（仅证据） */}
        {node.type === 'evidence' && node.source && (
          <section className="space-y-1.5">
            <h4 className="text-xs font-medium text-ink-muted uppercase tracking-wide">来源</h4>
            <p className="text-sm text-ink">{node.source}</p>
          </section>
        )}

        {/* 关联统计 */}
        {stats && (stats.positionCount || stats.skillCount || stats.evidenceCount) && (
          <section className="space-y-2">
            <h4 className="text-xs font-medium text-ink-muted uppercase tracking-wide">关联</h4>
            <dl className="grid grid-cols-3 gap-2 text-center">
              {stats.positionCount !== undefined && (
                <div className="rounded-md bg-subtle p-2">
                  <dt className="text-[10px] text-ink-muted">关联岗位</dt>
                  <dd className="text-sm font-mono text-ink tabular-nums">{stats.positionCount}</dd>
                </div>
              )}
              {stats.skillCount !== undefined && (
                <div className="rounded-md bg-subtle p-2">
                  <dt className="text-[10px] text-ink-muted">关联技能</dt>
                  <dd className="text-sm font-mono text-ink tabular-nums">{stats.skillCount}</dd>
                </div>
              )}
              {stats.evidenceCount !== undefined && (
                <div className="rounded-md bg-subtle p-2">
                  <dt className="text-[10px] text-ink-muted">关联证据</dt>
                  <dd className="text-sm font-mono text-ink tabular-nums">{stats.evidenceCount}</dd>
                </div>
              )}
            </dl>
          </section>
        )}

        {/* 描述 */}
        {node.description && (
          <section className="space-y-1.5">
            <h4 className="text-xs font-medium text-ink-muted uppercase tracking-wide">描述</h4>
            <p className="text-sm text-ink-secondary leading-relaxed">{node.description}</p>
          </section>
        )}

        {/* 跳转链接（占位 — 待真实 API 接入后启用） */}
        <section className="border-t border-border pt-3">
          <p className="text-xs text-ink-faint">
            下钻接口（M3 后端就绪后启用）：
          </p>
          <ul className="mt-1.5 space-y-1 text-xs font-mono text-ink-muted">
            {node.type === 'position' && (
              <li>GET /api/v1/graph/position/{node.id}/skills</li>
            )}
            {node.type === 'skill' && (
              <>
                <li>GET /api/v1/graph/skill/{node.id}/evidence</li>
                <li>GET /api/v1/graph/skill/{node.id}/positions</li>
              </>
            )}
          </ul>
        </section>
      </div>
    </div>
  )
}
