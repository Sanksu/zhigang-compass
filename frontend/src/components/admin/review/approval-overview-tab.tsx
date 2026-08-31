/**
 * 岗位审核「总览」Tab — 全审批池工作流面板（AdminReviewPage 首 Tab）
 *
 * 数据源 POST /admin/approvals/summary 只读聚合（不改任何状态机）：跨候选晋升/
 * 演化晋级/衰退归档/字典守卫/LLM 决策/技术观察池/技能别名，按 待办/需复核/已通过
 * 三阶段聚合。点击任一待办芯片深链至该审批流原审核页对应 Tab（复用既有审核逻辑）。
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { CheckCircle2, ClipboardList, ShieldAlert } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { apiGet } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { Schema } from './review-types'

type Summary = Schema['ApprovalSummaryData']
type Stream = Schema['ApprovalStreamSummary']

/* 三阶段列的语义映射：列 id → 计数字段 + 状态色（对齐岗位状态机色板） */
interface ColumnDef {
  readonly id: 'pending' | 'review' | 'approved'
  readonly label: string
  readonly field: keyof Pick<Stream, 'pending' | 'review' | 'approved'>
  readonly tone: string
}

const COLUMNS: ColumnDef[] = [
  { id: 'pending', label: '待办', field: 'pending', tone: 'text-state-candidate' },
  { id: 'review', label: '需复核 · 阻断', field: 'review', tone: 'text-state-declining' },
  { id: 'approved', label: '已通过 / 已生效', field: 'approved', tone: 'text-state-emerging' },
]

function KpiCard(props: {
  icon: 'pending' | 'review' | 'approved'
  value: number
  label: string
  focus?: boolean
}) {
  const Icon =
    props.icon === 'pending' ? ClipboardList : props.icon === 'review' ? ShieldAlert : CheckCircle2
  const tone =
    props.icon === 'pending'
      ? 'text-state-candidate'
      : props.icon === 'review'
        ? 'text-state-declining'
        : 'text-state-emerging'
  return (
    <Card className={cn(props.focus && 'border-border-strong')}>
      <CardContent className="py-4">
        <div className="flex items-center justify-between mb-2">
          <Icon className={cn('size-4', tone)} />
          <Badge
            variant={props.icon === 'review' ? 'declining' : props.icon === 'approved' ? 'emerging' : 'candidate'}
            className="text-[10px]"
          >
            {COLUMNS.find((c) => c.id === props.icon)?.label}
          </Badge>
        </div>
        <div className="text-2xl font-semibold tracking-tight tabular-nums">{props.value}</div>
        <div className="text-xs text-ink-muted mt-1">{props.label}</div>
      </CardContent>
    </Card>
  )
}

export function ApprovalOverviewTab() {
  const navigate = useNavigate()
  const [data, setData] = useState<Summary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiGet<Summary>('/admin/approvals/summary')
      .then(setData)
      .catch(() => setError('审批池汇总加载失败'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return <p className="py-12 text-center text-sm text-ink-muted">加载审批池汇总…</p>
  }
  if (error || !data) {
    return <p className="py-12 text-center text-sm text-state-archived">{error ?? '无数据'}</p>
  }

  return (
    <>
      {/* 统计卡（真实：来自只读聚合端点） */}
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
        <KpiCard focus icon="pending" value={data.summary.total_pending} label="跨审批流待处理" />
        <KpiCard icon="review" value={data.summary.total_review} label="低置信阻断 · 证据不足" />
        <KpiCard icon="approved" value={data.summary.total_approved} label="整体已通过 · 已生效" />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {COLUMNS.map((col) => {
          const items = data.streams.filter((s) => s[col.field] > 0)
          const total = items.reduce((acc, s) => acc + s[col.field], 0)
          return (
            <Card key={col.id}>
              <CardContent className="py-4">
                <div className="flex items-center justify-between mb-3">
                  <span className={cn('text-sm font-medium', col.tone)}>{col.label}</span>
                  <span className="text-xs font-mono text-ink-muted tabular-nums">{total}</span>
                </div>
                {items.length === 0 ? (
                  <p className="py-6 text-center text-xs text-ink-faint border border-dashed border-border rounded-md">
                    暂无该阶段审批项
                  </p>
                ) : (
                  <div>
                    {items.map((s) => (
                      <button
                        key={s.id}
                        type="button"
                        onClick={() => navigate(s.route)}
                        title={s.description}
                        className={cn(
                          'w-full flex items-center gap-2 px-3 py-2 mb-2 text-left rounded-md',
                          'border border-border bg-subtle hover:bg-elevated transition-colors',
                        )}
                      >
                        <span className={cn('size-2 shrink-0 rounded-full', col.tone)} />
                        <span className="text-sm font-medium text-ink truncate">{s.label}</span>
                        <span className="ml-auto text-xs font-mono text-ink-muted tabular-nums">
                          {s[col.field]}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          )
        })}
      </div>
    </>
  )
}