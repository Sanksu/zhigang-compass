import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { RefreshCw, ScrollText } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { apiGet, apiPost } from '@/lib/api'
import type { components } from '@/types/api'

type GovernanceSummary = components['schemas']['GraphGovernanceSummary']
type GovernanceDomain = components['schemas']['GraphGovernanceDomain']
type ResyncResult = components['schemas']['GraphGovernanceResyncResult']

function pct(v: number | null | undefined): string {
  return typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : 'n/a'
}

/** 归类依据来源 → 展示文案 */
const SOURCE_LABELS: Record<string, string> = {
  backbone: '骨干簇',
  pin_cluster: '骨干指派',
  pin: '治理指派',
  attach: '阈值归类',
  leftover_pin: '兜底指派',
  general_pin: '治理弃权',
  below_affinity: '证据不足',
  not_dominant: '多域拉扯',
  no_edges: '零投影边',
  leftover_no_edges: '投影外零证据',
}

/** 图谱域治理页（2026-08-31 域治理 PR 链前端入口）：
 * 域划分总览 + 共成员基准得分 + 自审待审徽标 + 后台重同步触发 */
export function AdminGraphGovernancePage() {
  const [data, setData] = useState<GovernanceSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [resyncing, setResyncing] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  // 拉取 + 轮询共用：running=true 时 3s 轮询直至后台任务结束
  useEffect(() => {
    let cancelled = false
    const tick = () => {
      apiGet<{ data: GovernanceSummary }>('/admin/graph-governance/summary')
        .then((res) => {
          if (cancelled) return
          setError(null)
          setData(res.data)
          setLoading(false)
          const running = res.data?.resync_running ?? false
          setResyncing((prev) => (prev !== running ? running : prev))
        })
        .catch(() => {
          if (cancelled) return
          setError('加载域治理总览失败，请稍后重试')
          setLoading(false)
        })
    }
    tick()
    const timer = setInterval(tick, 3000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [])

  async function triggerResync() {
    setNotice(null)
    try {
      const res = await apiPost<{ data: ResyncResult }>('/admin/graph-governance/resync')
      if (res.data?.started) {
        setResyncing(true)
        setNotice(res.data.message ?? '重同步已受理')
      }
    } catch {
      setNotice('触发失败：可能已有任务进行中')
    }
  }

  const semanticDomains = (data?.domains ?? []).filter((d) => !d.is_general)
  const generalDomain = (data?.domains ?? []).find((d) => d.is_general)
  const failures = data?.benchmark?.failures ?? []

  return (
    <div className="space-y-4">
      <PageHeader
        title="图谱域治理"
        description="岗位职能域划分总览 · 共成员基准 · 自审待审"
        actions={
          <div className="flex items-center gap-2">
            <Link to="/admin/llm-decisions?domain=cluster_membership">
              <Button variant="outline" size="sm" className="gap-1">
                <ScrollText className="size-4" />
                自审待审
                {(data?.membership_pending ?? 0) > 0 && (
                  <Badge variant="declining" className="ml-1">{data?.membership_pending}</Badge>
                )}
              </Button>
            </Link>
            <Button size="sm" onClick={() => void triggerResync()} disabled={resyncing} className="gap-1">
              <RefreshCw className={`size-4 ${resyncing ? 'animate-spin' : ''}`} />
              {resyncing ? '重同步进行中…' : '重跑域同步'}
            </Button>
          </div>
        }
      />

      {error && <Card><CardContent className="text-state-declining text-sm py-4">{error}</CardContent></Card>}
      {notice && <Card><CardContent className="text-ink-muted text-sm py-3">{notice}</CardContent></Card>}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card><CardHeader><CardTitle className="text-sm text-ink-muted">已划分岗位</CardTitle></CardHeader>
          <CardContent className="text-2xl font-semibold">{data?.positions ?? '—'}</CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm text-ink-muted">语义域</CardTitle></CardHeader>
          <CardContent className="text-2xl font-semibold">{data?.semantic_domains ?? '—'}</CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm text-ink-muted">弃权（通用域）</CardTitle></CardHeader>
          <CardContent className="text-2xl font-semibold">{data?.general_count ?? '—'}</CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm text-ink-muted">基准严格通过率</CardTitle></CardHeader>
          <CardContent className="text-2xl font-semibold">
            {pct(data?.benchmark?.strict_accuracy)}
            <div className="text-xs text-ink-muted font-normal">
              共成员对级 F1 {data?.benchmark?.pairwise_f1 != null ? data.benchmark.pairwise_f1.toFixed(3) : 'n/a'}
            </div>
          </CardContent></Card>
      </div>

      <Card>
        <CardHeader><CardTitle className="text-base">语义域划分</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {loading && <div className="text-sm text-ink-muted py-4">加载中…</div>}
          {!loading && semanticDomains.length === 0 && (
            <div className="text-sm text-ink-muted py-4">暂无域划分数据（可点击「重跑域同步」生成）</div>
          )}
          {semanticDomains.map((d: GovernanceDomain) => (
            <div key={d.domain_id} className="rounded-lg border border-line-subtle px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{d.domain_name ?? d.domain_id}</span>
                <Badge variant="outline">{d.member_count ?? 0}</Badge>
                <span className="text-xs text-ink-muted ml-auto">
                  {Object.entries(d.source_counts ?? {})
                    .map(([k, n]) => `${SOURCE_LABELS[k] ?? k}×${n}`)
                    .join(' · ')}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                {(d.members ?? []).map((m) => (
                  <span key={m.name} className="text-xs text-ink-muted bg-bg-subtle rounded px-1.5 py-0.5">
                    {m.name}
                    {m.source === 'pin' || m.source === 'pin_cluster' || m.source === 'leftover_pin' ? '※' : ''}
                  </span>
                ))}
                {(d.member_count ?? 0) > (d.members ?? []).length && (
                  <span className="text-xs text-ink-muted">…共 {d.member_count} 岗</span>
                )}
              </div>
            </div>
          ))}
          {generalDomain && (
            <div className="text-xs text-ink-muted">
              通用弃权域 {generalDomain.member_count} 岗（证据不足或无同族，诚实不强行归属）
              {' · ' + Object.entries(generalDomain.source_counts ?? {}).map(([k, n]) => `${SOURCE_LABELS[k] ?? k}×${n}`).join(' · ')}
            </div>
          )}
        </CardContent>
      </Card>

      {failures.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="text-base">基准未通过项（{failures.length}）</CardTitle></CardHeader>
          <CardContent className="space-y-1.5">
            {failures.slice(0, 10).map((f) => (
              <div key={f.position} className="text-sm">
                <span className="font-medium">{f.position}</span>
                <span className="text-ink-muted"> — {f.detail}</span>
              </div>
            ))}
            {failures.length > 10 && (
              <div className="text-xs text-ink-muted">…其余 {failures.length - 10} 项见评测报告</div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
