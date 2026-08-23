import { ChevronDown, RotateCcw, SlidersHorizontal } from 'lucide-react'
import { useState } from 'react'
import type { PositionStatus } from './types'
import { GRAPH_STATUS_META, GRAPH_STATUS_ORDER } from './graph-visual-tokens'
import { cn } from '@/lib/utils'

const ALL_STATUSES: { value: PositionStatus; label: string; color: string }[] = GRAPH_STATUS_ORDER.map((value) => ({
  value,
  ...GRAPH_STATUS_META[value],
}))

interface GraphFilterPanelProps {
  minWeight: number
  onMinWeightChange: (value: number) => void
  hiddenStatuses?: Set<PositionStatus>
  onToggleStatus?: (status: PositionStatus) => void
  showOnlyMustEdges?: boolean
  onToggleMustEdges?: (value: boolean) => void
  hideSoftSkills?: boolean
  onToggleSoftSkills?: (value: boolean) => void
  onReset?: () => void
  visibleCount?: number
  hiddenCount?: number
}

export function GraphFilterPanel({
  minWeight,
  onMinWeightChange,
  hiddenStatuses = new Set(),
  onToggleStatus,
  showOnlyMustEdges = false,
  onToggleMustEdges,
  hideSoftSkills = false,
  onToggleSoftSkills,
  onReset,
  visibleCount,
  hiddenCount,
}: GraphFilterPanelProps) {
  const [expanded, setExpanded] = useState(false)
  const activeFilterCount = Number(minWeight > 0) + hiddenStatuses.size + Number(showOnlyMustEdges) + Number(hideSoftSkills)

  return (
    <section className="absolute left-3 top-3 z-20 w-[calc(100%-1.5rem)] max-w-64 overflow-hidden rounded-lg border border-border/80 bg-canvas/90 shadow-md backdrop-blur-xl sm:left-4 sm:top-4 sm:w-56" aria-label="图层探索器">
      <button
        type="button"
        className="flex min-h-10 w-full items-center gap-2 px-3 text-left hover:bg-subtle/80"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        aria-controls="graph-layer-controls"
      >
        <SlidersHorizontal className="size-3.5 text-ink-muted" />
        <span className="flex-1 text-xs font-semibold text-ink">图层探索器</span>
        {activeFilterCount > 0 && (
          <span className="rounded-full bg-ink px-1.5 py-0.5 font-mono text-[9px] text-canvas" aria-label={`${activeFilterCount} 个过滤条件已启用`}>
            {activeFilterCount}
          </span>
        )}
        {typeof visibleCount === 'number' && <span className="font-mono text-[10px] text-ink-faint">{visibleCount} 可见</span>}
        <ChevronDown className={cn('size-3.5 text-ink-faint transition-transform', expanded && 'rotate-180')} />
      </button>

      {expanded && (
        <div id="graph-layer-controls" className="max-h-[min(70vh,480px)] space-y-4 overflow-y-auto border-t border-border/70 px-3 py-3">
          <div>
            <div className="mb-2 flex items-center justify-between">
              <label htmlFor="graph-min-weight" className="text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-muted">关系强度</label>
              <span className="font-mono text-[10px] text-ink-secondary">≥ {minWeight}</span>
            </div>
            <input
              id="graph-min-weight"
              type="range"
              min={0}
              max={100}
              step={1}
              value={minWeight}
              onChange={(event) => onMinWeightChange(Number(event.target.value))}
              className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-border accent-primary outline-none"
            />
          </div>

          {onToggleStatus && (
            <fieldset>
              <legend className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-muted">岗位状态</legend>
              <div className="grid grid-cols-2 gap-1">
                {ALL_STATUSES.map((status) => {
                  const visible = !hiddenStatuses.has(status.value)
                  return (
                    <label key={status.value} className={cn('flex cursor-pointer items-center gap-2 rounded-md border px-2 py-1.5 text-[11px] transition-colors', visible ? 'border-border bg-canvas text-ink-secondary' : 'border-transparent bg-subtle text-ink-faint line-through')}>
                      <input
                        type="checkbox"
                        checked={visible}
                        onChange={() => onToggleStatus(status.value)}
                        className="sr-only"
                        aria-label={`显示${status.label}岗位`}
                      />
                      <span className="size-2 shrink-0 rounded-full" style={{ backgroundColor: status.color }} />
                      {status.label}
                    </label>
                  )
                })}
              </div>
            </fieldset>
          )}

          <fieldset>
            <legend className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-muted">技能与关系</legend>
            <div className="space-y-1">
              {onToggleSoftSkills && (
                <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-[11px] text-ink-secondary hover:bg-subtle">
                  <input type="checkbox" checked={!hideSoftSkills} onChange={(event) => onToggleSoftSkills(!event.target.checked)} className="size-3 rounded accent-primary" aria-label="显示软技能" />
                  <span className="size-2 rounded-full bg-graph-soft-skill" />
                  显示软技能
                </label>
              )}
              {onToggleMustEdges && (
                <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-[11px] text-ink-secondary hover:bg-subtle">
                  <input type="checkbox" checked={showOnlyMustEdges} onChange={(event) => onToggleMustEdges(event.target.checked)} className="size-3 rounded accent-primary" />
                  <span className="h-0.5 w-4 bg-ink/60" aria-hidden="true" />
                  仅看必备关系
                </label>
              )}
            </div>
          </fieldset>

          <div className="flex items-center justify-between border-t border-border/70 pt-2">
            <span className="text-[10px] text-ink-faint">
              {hiddenCount ? `淡出 ${hiddenCount} 个节点` : '全部图层可见'}
            </span>
            <button type="button" onClick={onReset} disabled={activeFilterCount === 0} className="flex items-center gap-1 rounded px-1.5 py-1 text-[10px] font-medium text-ink-muted hover:bg-subtle hover:text-ink disabled:cursor-default disabled:opacity-35">
              <RotateCcw className="size-3" />
              重置
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
