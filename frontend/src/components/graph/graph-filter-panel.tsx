import { SlidersHorizontal } from 'lucide-react'
import type { PositionStatus } from './types'

const ALL_STATUSES: { value: PositionStatus; label: string; color: string }[] = [
  { value: 'emerging', label: '新兴', color: '#10b981' },
  { value: 'stable', label: '稳定', color: '#3b82f6' },
  { value: 'candidate', label: '候选', color: '#71717a' },
  { value: 'declining', label: '衰退', color: '#f59e0b' },
  { value: 'archived', label: '归档', color: '#ef4444' },
]

interface GraphFilterPanelProps {
  minWeight: number
  onMinWeightChange: (v: number) => void
  /** B2: 隐藏的岗位状态集合（勾选 = 显示，去勾 = 隐藏） */
  hiddenStatuses?: Set<PositionStatus>
  onToggleStatus?: (s: PositionStatus) => void
  /** B2: true = 仅显示 must（必备）边，false = 显示全部 */
  showOnlyMustEdges?: boolean
  onToggleMustEdges?: (v: boolean) => void
}

export function GraphFilterPanel({
  minWeight,
  onMinWeightChange,
  hiddenStatuses = new Set(),
  onToggleStatus,
  showOnlyMustEdges = false,
  onToggleMustEdges,
}: GraphFilterPanelProps) {
  return (
    <div className="absolute left-4 top-4 z-10 w-52 rounded-xl border border-white/10 bg-white/75 p-3.5 shadow-xl backdrop-blur-md dark:border-white/5 dark:bg-black/55">
      <div className="mb-3 flex items-center gap-2 text-xs font-semibold text-ink">
        <SlidersHorizontal className="size-3.5" />
        图谱过滤
      </div>

      {/* 最小权重 */}
      <div className="mb-3">
        <div className="mb-1.5 flex items-center justify-between">
          <label className="text-[11px] font-medium text-ink-muted">最小权重</label>
          <span className="rounded bg-subtle px-1.5 py-0.5 text-[10px] font-mono text-ink-secondary">
            {minWeight}
          </span>
        </div>
        <input
          type="range"
          min={0}
          max={100}
          step={1}
          value={minWeight}
          onChange={(e) => onMinWeightChange(Number(e.target.value))}
          className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-border accent-primary outline-none"
        />
      </div>

      {/* B2: 岗位状态过滤 */}
      {onToggleStatus && (
        <div className="mb-3">
          <p className="mb-1.5 text-[11px] font-medium text-ink-muted">岗位状态</p>
          <div className="space-y-1">
            {ALL_STATUSES.map((s) => {
              const visible = !hiddenStatuses.has(s.value)
              return (
                <label
                  key={s.value}
                  className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-[11px] text-ink-secondary hover:bg-subtle/60"
                >
                  <input
                    type="checkbox"
                    checked={visible}
                    onChange={() => onToggleStatus(s.value)}
                    className="size-3 rounded accent-primary"
                  />
                  <span
                    className="size-2 shrink-0 rounded-full"
                    style={{ backgroundColor: s.color }}
                  />
                  {s.label}
                </label>
              )
            })}
          </div>
        </div>
      )}

      {/* B2: 边关系过滤（must 必备 / nice 加分） */}
      {onToggleMustEdges && (
        <div>
          <p className="mb-1.5 text-[11px] font-medium text-ink-muted">边关系</p>
          <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-[11px] text-ink-secondary hover:bg-subtle/60">
            <input
              type="checkbox"
              checked={showOnlyMustEdges}
              onChange={(e) => onToggleMustEdges(e.target.checked)}
              className="size-3 rounded accent-primary"
            />
            仅显示必备关系
          </label>
        </div>
      )}
    </div>
  )
}
