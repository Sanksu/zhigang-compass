/** 演化视图组件（从 evolution-page.tsx 抽出，第六轮审查拆分：页面 ≤800 行惯例）。 */
import { useEffect, useState } from 'react'
import { GitBranch, Boxes, TrendingDown } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge, type BadgeProps } from '@/components/ui/badge'
import {
  PositionStateBadge,
  POSITION_STATE_DOT,
  POSITION_STATE_META,
  type PositionState,
} from '@/components/shared/position-state-badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { apiGet, errMsg } from '@/lib/api'
import type { components } from '@/types/api'
import type { EvolutionEvent, EvolutionEventListData, EvolutionVersion } from './types'

export type StateMachineData = components['schemas']['StateMachineData']

/** 发现状态机六态（label/badge 复用 shared POSITION_STATE_META；active=图谱常态岗位，不入候选池分发） */
const MACHINE_STATES = ['candidate', 'emerging', 'stable', 'declining', 'archived', 'rejected'] as const

/** 六态分布 + 最近流转记录（GET /evolution/state-machine） */

export function StateMachineView() {
  const [data, setData] = useState<StateMachineData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiGet<StateMachineData>('/evolution/state-machine')
      .then(setData)
      .catch(() => setError('状态机流转记录加载失败'))
  }, [])

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <GitBranch className="size-4" />
          岗位状态机流转
          <span className="text-[11px] font-normal text-ink-faint">
            六态生命周期 · 人工审核流转记录（自动流转不写审计，见后端说明）
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <p className="py-4 text-center text-xs text-state-archived">{error}</p>}
        {!error && !data && <p className="py-4 text-center text-xs text-ink-faint">加载中…</p>}
        {!error && data && (
          <>
            {/* 六态分布（真实候选池状态聚合） */}
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
              {MACHINE_STATES.map((state) => {
                const meta = POSITION_STATE_META[state]
                return (
                  <div key={state} className="rounded-md border border-border p-2.5">
                    <div className="flex items-center gap-1.5 text-xs font-medium text-ink">
                      <span className={`size-2 rounded-full ${POSITION_STATE_DOT[state as PositionState] ?? ''}`} />
                      {meta.label}
                    </div>
                    <div className="mt-1 text-xl font-semibold tabular-nums">{data.states[state] ?? 0}</div>
                  </div>
                )
              })}
            </div>
            {/* 最近人工流转记录（audit_logs discovery.state_transition） */}
            <div>
              <h4 className="mb-2 text-xs font-medium text-ink-muted uppercase tracking-wide">最近人工流转</h4>
              {data.transitions.length === 0 ? (
                <p className="py-6 text-center text-xs text-ink-faint border border-dashed border-border rounded-md">
                  暂无流转记录（人工审核后在此展示）
                </p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>时间</TableHead>
                      <TableHead>岗位</TableHead>
                      <TableHead>流转</TableHead>
                      <TableHead>操作者</TableHead>
                      <TableHead>原因</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.transitions.map((t) => (
                      <TableRow key={t.id}>
                        <TableCell className="text-xs font-mono text-ink-muted whitespace-nowrap">
                          {t.created_at ? t.created_at.replace('T', ' ').slice(0, 16) : '—'}
                        </TableCell>
                        <TableCell className="text-xs font-medium text-ink max-w-40 truncate">
                          {t.position_name}
                        </TableCell>
                        <TableCell className="text-xs">
                          <span className="inline-flex items-center gap-1">
                            <PositionStateBadge state={t.from_state ?? ''} label={t.from_state ?? undefined} className="text-[10px]" />
                            <span className="text-ink-faint">→</span>
                            <PositionStateBadge state={t.to_state ?? ''} label={t.to_state ?? undefined} className="text-[10px]" />
                          </span>
                        </TableCell>
                        <TableCell className="text-xs text-ink-secondary">{t.operator}</TableCell>
                        <TableCell className="text-xs text-ink-muted max-w-48 truncate">{t.reason || '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

// ===== EvolutionEventsView =====

/** 谱系事件流（真实 GET /evolution/events，机制补强② born/merged/ended） */
const EVENT_META: Record<
  string,
  { label: string; tone: string; badge: BadgeProps['variant']; desc: string }
> = {
  born: { label: '新增', tone: 'bg-state-emerging', badge: 'emerging', desc: '主键改名/新实体出现' },
  merged: { label: '合并', tone: 'bg-state-active', badge: 'outline', desc: '多个实体归一' },
  ended: { label: '终结', tone: 'bg-state-declining', badge: 'declining', desc: '实体消亡/弃用' },
}

/** 谱系事件流（新增/合并/终结）——真实 GET /evolution/events */
export function EvolutionEventsView() {
  const [data, setData] = useState<EvolutionEvent[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    apiGet<EvolutionEventListData>('/evolution/events?limit=50')
      .then((r) => {
        if (!cancelled) setData(r.items)
      })
      .catch((e) => {
        if (!cancelled) setError(errMsg(e, '谱系事件加载失败'))
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Boxes className="size-4" />
          <span>谱系事件流</span>
          <span className="text-[11px] font-normal text-ink-faint">
            实体新增 / 合并 / 终结 · 自动流转不写人工审计（见后端说明）
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {error && <p className="py-6 text-center text-xs text-state-archived">{error}</p>}
        {!error && data === null && (
          <p className="py-6 text-center text-xs text-ink-faint">加载谱系事件…</p>
        )}
        {!error && data !== null && data.length === 0 && (
          <p className="py-6 text-center text-xs text-ink-faint">暂无谱系事件（版本足够多时自动产生）</p>
        )}
        {!error && data !== null && data.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[140px]">时间</TableHead>
                <TableHead>类型</TableHead>
                <TableHead>变更</TableHead>
                <TableHead className="w-[170px]">版本</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((ev) => {
                const meta = EVENT_META[ev.event_type] ?? null
                const target = ev.to_name || ev.from_name || '—'
                const source = ev.from_name
                return (
                  <TableRow key={ev.id}>
                    <TableCell className="text-xs font-mono text-ink-muted whitespace-nowrap">
                      {ev.created_at ? ev.created_at.replace('T', ' ').slice(0, 16) : '—'}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={meta?.badge ?? 'outline'}
                        className="text-[11px] inline-flex items-center gap-1"
                      >
                        <span className={`size-1.5 rounded-full ${meta?.tone ?? ''}`} />
                        {meta?.label ?? ev.event_type}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs">
                      <span className="text-ink font-medium">{target}</span>
                      {source && (
                        <span className="text-ink-faint">
                          {' '}（原 {source}）
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-[11px] text-ink-faint">{ev.version_id}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}

// ===== DataWarningBanner =====

type DataWarningEntry = NonNullable<EvolutionVersion['data_warning']>[string]

const WARNING_DIM_LABEL: Record<string, string> = {
  positions: '岗位样本量',
  requires_edges: 'REQUIRES 关系量',
}

/** 较上版变化幅度（ratio = cur/prev）：萎缩显示 -N%，激增显示 +N% */
function warningDeltaPct(e: DataWarningEntry): string {
  if (e.ratio == null) return '—'
  const pct = Math.round(Math.abs(1 - e.ratio) * 100)
  return `${e.direction === 'surged' ? '+' : '-'}${pct}%`
}

/** 样本量对比告警（机制补强①：岗位/关系量比上版萎缩 <50% 或膨胀 >200%） */
export function DataWarningBanner({ warning }: { warning: NonNullable<EvolutionVersion['data_warning']> }) {
  const entries = Object.entries(warning).map(([dim, w]: [string, DataWarningEntry]) => ({
    dim,
    label: WARNING_DIM_LABEL[dim] ?? dim,
    ...w,
  }))
  if (entries.length === 0) return null
  return (
    <div className="mb-4 flex flex-col gap-2 rounded-md border border-state-declining/40 bg-state-declining/10 p-3">
      <div className="flex items-center gap-2 text-xs font-medium text-state-declining">
        <TrendingDown className="size-4" />
        <span>样本量波动告警</span>
        <span className="font-normal text-ink-faint">与上一版本比萎缩 &lt;50% 或膨胀 &gt;200%，Z-score 信号可能失真，请人工核对采集</span>
      </div>
      <ul className="space-y-1 text-xs text-ink-secondary">
        {entries.map((e) => (
          <li key={e.dim} className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-ink">{e.label}</span>
            <Badge variant={e.direction === 'shrunk' ? 'declining' : 'emerging'} className="text-[11px]">
              {e.direction === 'shrunk' ? '萎缩' : '激增'}
            </Badge>
            <span className="font-mono text-ink-faint">
              {e.prev ?? '—'} → {e.cur ?? '—'}（较上版 {warningDeltaPct(e)}）
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

