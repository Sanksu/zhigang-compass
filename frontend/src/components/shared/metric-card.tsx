/** 统一指标卡 — 融合 evolution `MetricCard` 与 dashboard `StatItem` 两种形态。
 * 唯一差异：dashboard 带 icon + 无底部色条；evolution 带 delta 数字 + 底部色条。
 * 以 `icon` / `bar` 两个可选项承接差异，单一实现两处复用。 */
import type { LucideIcon } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'

export type MetricTone = 'emerging' | 'declining' | 'stable' | 'muted'

const TONE_TEXT: Record<MetricTone, string> = {
  emerging: 'text-state-emerging',
  declining: 'text-state-declining',
  stable: 'text-state-stable',
  muted: 'text-ink-muted',
}

const TONE_BAR: Record<MetricTone, string> = {
  emerging: 'bg-state-emerging/10',
  declining: 'bg-state-declining/10',
  stable: 'bg-state-stable/10',
  muted: 'bg-subtle',
}

export interface MetricCardData {
  label: string
  value: string | number
  /** number → 以 +/- 前缀展示；string → 原样（如「5 边」） */
  delta?: string | number
  deltaTone?: MetricTone
  hint?: string
  icon?: LucideIcon
  /** 是否渲染底部色调条（evolution 样式；dashboard 无此趋势条） */
  bar?: boolean
}

export function MetricCard({ data }: { data: MetricCardData }) {
  const Icon = data.icon
  const tone = data.deltaTone ?? 'muted'
  return (
    <Card>
      <CardContent className="py-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="flex items-center gap-1.5 text-xs text-ink-muted">
            {Icon && <Icon className="size-4 text-ink-faint" />}
            {data.label}
          </span>
          {data.delta != null && (
            <span className={cn('inline-flex items-center gap-0.5 text-xs font-mono', TONE_TEXT[tone])}>
              {typeof data.delta === 'number' ? `${data.delta > 0 ? '+' : ''}${data.delta}` : data.delta}
            </span>
          )}
        </div>
        <div className="text-2xl font-semibold tracking-tight tabular-nums">
          {typeof data.value === 'number' ? (
            data.value.toLocaleString()
          ) : (
            <span className="font-mono">{data.value}</span>
          )}
        </div>
        {data.hint && <div className="mt-1 truncate text-[10px] text-ink-faint">{data.hint}</div>}
        {data.bar && <div className={cn('mt-2 h-0.5 rounded-full', TONE_BAR[tone])} />}
      </CardContent>
    </Card>
  )
}