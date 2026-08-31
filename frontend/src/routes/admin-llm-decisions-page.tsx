import { useEffect, useMemo, useState } from 'react'
import { ClipboardCheck, EyeOff, ShieldAlert, ShieldCheck } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { SkillAliasesTable } from '@/components/admin/llm/skill-aliases-table'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { PaginationBar } from '@/components/ui/pagination'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { apiGet, apiPost } from '@/lib/api'
import { formatDateTime } from '@/lib/utils'
import { useIsDesktop } from '@/hooks/use-media-query'
import type { components } from '@/types/api'

type LlmDecisionItem = components['schemas']['LlmDecisionItem']
type DomainSummary = components['schemas']['LlmDecisionDomainSummary']

const PAGE_SIZE = 20

const DOMAIN_LABELS: Record<string, string> = {
  jd_extract: 'JD 抽取',
  position_normalize: '岗位名归一',
  skill_normalize: '技能名归一',
  position_classify: '岗位分类',
  cluster_label: '簇命名',
  skill_classify: '技能分类',
  governance: '自动化治理',
  skill_relation: '技能关系',
}

const STATUS_TONE: Record<string, string> = {
  shadow: 'text-state-emerging',
  proposal: 'text-state-candidate',
  auto_applied: 'text-state-stable',
  blocked: 'text-state-declining',
  rejected: 'text-state-declining',
  approved: 'text-state-stable',
}

const TIER_TONE: Record<string, string> = {
  R0: 'text-state-stable',
  R1: 'text-state-candidate',
  R2: 'text-state-declining',
}

/** 决策信封只读页：验收卡片（domain×status 汇总）+ 决策记录列表 */
export function AdminLlmDecisionsPage() {
  const [items, setItems] = useState<LlmDecisionItem[]>([])
  const [total, setTotal] = useState(0)
  const [summary, setSummary] = useState<{ by_domain: DomainSummary[]; totals: Record<string, number> } | null>(null)
  const [page, setPage] = useState(1)
  const [domain, setDomain] = useState('')
  const [status, setStatus] = useState('')
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const isDesktop = useIsDesktop()

  const params = useMemo(() => {
    const p = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) })
    if (domain) p.set('domain', domain)
    if (status) p.set('status', status)
    return p.toString()
  }, [domain, status, page])

  useEffect(() => {
    let cancelled = false
    apiGet<{ items: LlmDecisionItem[]; total: number; limit: number; offset: number }>(`/admin/llm-decisions?${params}`)
      .then((res) => {
        if (cancelled) return
        setError(null)
        setItems(res.items)
        setTotal(res.total)
      })
      .catch(() => {
        if (!cancelled) setError('决策记录加载失败，请确认后端已运行决策流水')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [params])

  useEffect(() => {
    let cancelled = false
    apiGet<{ by_domain: DomainSummary[]; totals: Record<string, number> }>('/admin/llm-decisions/summary')
      .then((res) => {
        if (!cancelled) setSummary(res)
      })
      .catch(() => {
        /* 汇总卡片 best-effort，不影响列表 */
      })
    return () => {
      cancelled = true
    }
  }, [])

  function applyFilter(next: { domain?: string; status?: string }) {
    if (next.domain !== undefined) setDomain(next.domain)
    if (next.status !== undefined) setStatus(next.status)
    setPage(1)
  }

  const totals = summary?.totals ?? null
  const cards = [
    { label: '待审提案', value: totals?.proposal ?? 0, tone: 'text-state-candidate', icon: ClipboardCheck },
    { label: '自动生效', value: totals?.auto_applied ?? 0, tone: 'text-state-stable', icon: ShieldCheck },
    { label: '硬门拦截', value: totals?.blocked ?? 0, tone: 'text-state-declining', icon: ShieldAlert },
    { label: 'Shadow 记录', value: totals?.shadow ?? 0, tone: 'text-state-emerging', icon: EyeOff },
  ]

  const filtered = q.trim()
    ? items.filter((it) => `${it.entity_id ?? ''} ${it.entity_type ?? ''} ${it.run_id ?? ''}`.toLowerCase().includes(q.trim().toLowerCase()))
    : items

  // 可批准/驳回状态：proposal（规范人工档）或 skill_classify 的 shadow（验收档晋升权威）
  function canReview(it: LlmDecisionItem): boolean {
    const mutable = ['skill_relation', 'position_normalize', 'skill_normalize', 'skill_classify']
    if (!mutable.includes(it.domain ?? '')) return false
    if (it.domain === 'skill_classify') return it.status === 'shadow'
    return it.status === 'proposal'
  }

  /** skill_normalize 记录的建议目标（审批关键信息：变体要并到哪）。
   *  kind=alias（别名回写）→ target_standard；归一图变异 → canonical_name/target_standard。 */
  function suggestTarget(it: LlmDecisionItem): { target: string; isAlias: boolean } | null {
    if (it.domain !== 'skill_normalize') return null
    const out = (it.structured_output ?? {}) as Record<string, unknown>
    const target = str(out['target_standard']) || str(out['canonical_name'])
    if (!target) return null
    return { target, isAlias: out['kind'] === 'alias' }
  }

  function str(v: unknown): string {
    return typeof v === 'string' ? v : ''
  }

  const [reviewing, setReviewing] = useState<{ id: string; entity: string; action: 'approve' | 'reject' } | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  async function submitReview() {
    if (!reviewing) return
    if (!reason.trim()) {
      setActionError('审批必须填写 review_reason')
      return
    }
    setBusy(true)
    setActionError(null)
    try {
      const url = `/admin/llm-decisions/${reviewing.id}/${reviewing.action}`
      await apiPost(url, { review_reason: reason.trim() })
      setReviewing(null)
      setReason('')
      // 刷新列表与汇总
      const res = await apiGet<{ items: LlmDecisionItem[]; total: number; limit: number; offset: number }>(`/admin/llm-decisions?${params}`)
      setItems(res.items)
      setTotal(res.total)
      const s = await apiGet<{ by_domain: DomainSummary[]; totals: Record<string, number> }>('/admin/llm-decisions/summary')
      setSummary(s)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '审批失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="LLM 决策与验收"
        description="六域 LLM 决策统一透明：JD 抽取 / 名称归一 / 分类 / 治理 / 技能关系 —— shadow·提案·自动生效·硬门拦截全部可追溯可回放"
      />

      <Tabs defaultValue="decisions">
        <TabsList className="mb-2">
          <TabsTrigger value="decisions" className="text-xs">决策与验收</TabsTrigger>
          <TabsTrigger value="aliases" className="text-xs">动态别名表</TabsTrigger>
        </TabsList>
        <TabsContent value="decisions" className="space-y-6">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {cards.map((c) => (
          <Card key={c.label}>
            <CardContent className="flex items-center gap-3 p-4">
              <c.icon className={`h-8 w-8 ${c.tone}`} />
              <div>
                <div className={`text-2xl font-bold ${c.tone}`}>{c.value}</div>
                <div className="text-xs text-muted-foreground">{c.label}</div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between space-y-0">
          <CardTitle>决策记录</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              placeholder="搜索 entity / run_id"
              className="h-8 w-full sm:w-52"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <Select
              value={domain || 'all'}
              onValueChange={(v) => applyFilter({ domain: v === 'all' ? '' : v })}
            >
              <SelectTrigger aria-label="域过滤" className="h-8 w-full sm:w-36 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部域</SelectItem>
                {Object.entries(DOMAIN_LABELS).map(([k, v]) => (
                  <SelectItem key={k} value={k}>
                    {v}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={status || 'all'}
              onValueChange={(v) => applyFilter({ status: v === 'all' ? '' : v })}
            >
              <SelectTrigger aria-label="状态过滤" className="h-8 w-full sm:w-36 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                {['shadow', 'proposal', 'auto_applied', 'blocked', 'approved', 'rejected'].map((s) => (
                  <SelectItem key={s} value={s}>
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button variant="ghost" size="sm" onClick={() => setQ('')}>
              清除
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {error ? (
            <p className="py-8 text-center text-sm text-state-declining">{error}</p>
          ) : isDesktop ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>域</TableHead>
                  <TableHead>实体</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>风险档</TableHead>
                  <TableHead>置信度</TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead>创建时间</TableHead>
                  <TableHead>操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading && filtered.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">
                      加载中…
                    </TableCell>
                  </TableRow>
                ) : filtered.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">
                      无匹配的决策记录
                    </TableCell>
                  </TableRow>
                ) : (
                  filtered.map((it) => (
                    <TableRow key={it.id}>
                      <TableCell>{DOMAIN_LABELS[it.domain ?? ""] ?? it.domain ?? "-"}</TableCell>
                      <TableCell className="max-w-[260px]">
                        <div className="flex flex-col">
                          <span className="truncate">
                            {it.entity_type ? `${it.entity_type}:` : ''}
                            {it.entity_id || '-'}
                          </span>
                          {(() => {
                            const sug = suggestTarget(it)
                            if (!sug) return null
                            return (
                              <span className="flex items-center gap-1 truncate text-xs text-muted-foreground">
                                <span className="text-state-candidate">→</span>
                                <span className="truncate">{sug.target}</span>
                                {sug.isAlias && (
                                  <Badge variant="outline" className="h-4 px-1 text-[10px] text-state-emerging">
                                    别名
                                  </Badge>
                                )}
                              </span>
                            )
                          })()}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className={`text-[11px] ${STATUS_TONE[it.status ?? ''] ?? ''}`}>
                          {it.status}
                        </Badge>
                      </TableCell>
                      <TableCell className={`text-xs font-semibold ${TIER_TONE[it.risk_tier ?? ''] ?? ''}`}>
                        {it.risk_tier || '-'}
                      </TableCell>
                      <TableCell>{it.confidence != null ? it.confidence.toFixed(2) : '-'}</TableCell>
                      <TableCell className="text-xs">{it.provider || '-'}</TableCell>
                      <TableCell className="text-xs">{it.created_at ? formatDateTime(it.created_at) : '-'}</TableCell>
                      <TableCell>
                        {canReview(it) ? (
                          <div className="flex items-center gap-1.5">
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-7 text-xs"
                              onClick={() => {
                                setActionError(null)
                                setReviewing({ id: it.id, entity: `${it.entity_type ?? ''}:${it.entity_id ?? ''}`, action: 'approve' })
                              }}
                            >
                              批准
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              className="h-7 text-xs text-state-declining"
                              onClick={() => {
                                setActionError(null)
                                setReviewing({ id: it.id, entity: `${it.entity_type ?? ''}:${it.entity_id ?? ''}`, action: 'reject' })
                              }}
                            >
                              驳回
                            </Button>
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground">-</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          ) : (
            <div className="space-y-3">
              {loading ? (
                <p className="py-8 text-center text-sm text-muted-foreground">加载中…</p>
              ) : filtered.length === 0 ? (
                <p className="py-8 text-center text-sm text-muted-foreground">无匹配的决策记录</p>
              ) : (
                filtered.map((it) => {
                  const sug = suggestTarget(it)
                  return (
                    <div key={it.id} className="rounded-lg border border-border p-3 space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-medium text-ink">
                          {DOMAIN_LABELS[it.domain ?? ''] ?? it.domain ?? '-'}
                        </span>
                        <Badge variant="outline" className={`text-[11px] ${STATUS_TONE[it.status ?? ''] ?? ''}`}>
                          {it.status}
                        </Badge>
                      </div>
                      <div className="min-w-0">
                        <span className="text-sm text-ink">
                          {it.entity_type ? `${it.entity_type}:` : ''}
                          {it.entity_id || '-'}
                        </span>
                        {sug && (
                          <span className="flex items-center gap-1 truncate text-xs text-muted-foreground mt-0.5">
                            <span className="text-state-candidate">→</span>
                            <span className="truncate">{sug.target}</span>
                            {sug.isAlias && (
                              <Badge variant="outline" className="h-4 px-1 text-[10px] text-state-emerging">
                                别名
                              </Badge>
                            )}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-3 text-xs text-ink-muted">
                        <span>风险档：<span className={`font-semibold ${TIER_TONE[it.risk_tier ?? ''] ?? ''}`}>{it.risk_tier || '-'}</span></span>
                        <span>置信度：<span className="font-mono">{it.confidence != null ? it.confidence.toFixed(2) : '-'}</span></span>
                        <span>Provider：<span>{it.provider || '-'}</span></span>
                      </div>
                      <div className="flex items-center justify-between gap-2 pt-1 border-t border-border">
                        <span className="text-[11px] text-ink-faint font-mono">
                          {it.created_at ? formatDateTime(it.created_at) : '-'}
                        </span>
                        {canReview(it) ? (
                          <div className="flex items-center gap-1.5">
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-7 text-xs"
                              onClick={() => {
                                setActionError(null)
                                setReviewing({ id: it.id, entity: `${it.entity_type ?? ''}:${it.entity_id ?? ''}`, action: 'approve' })
                              }}
                            >
                              批准
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              className="h-7 text-xs text-state-declining"
                              onClick={() => {
                                setActionError(null)
                                setReviewing({ id: it.id, entity: `${it.entity_type ?? ''}:${it.entity_id ?? ''}`, action: 'reject' })
                              }}
                            >
                              驳回
                            </Button>
                          </div>
                        ) : null}
                      </div>
                    </div>
                  )
                })
              )}
            </div>
          )}
          <PaginationBar
            page={page}
            pageSize={PAGE_SIZE}
            total={total}
            onPageChange={setPage}
          />
        </CardContent>
      </Card>

      {reviewing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/60" role="dialog" aria-modal="true">
          <Card className="w-full max-w-md">
            <CardHeader>
              <CardTitle>{reviewing.action === 'approve' ? '批准' : '驳回'}决策记录</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">
                {reviewing.action === 'approve' ? '批准后' : '驳回将仅流转状态'}：{' '}
                <span className="font-mono text-xs">{reviewing.entity}</span>
              </p>
              <Input
                placeholder="review_reason（必填）"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                autoFocus
              />
              {actionError && <p className="text-xs text-state-declining">{actionError}</p>}
              <div className="flex justify-end gap-2">
                <Button variant="ghost" size="sm" disabled={busy} onClick={() => setReviewing(null)}>
                  取消
                </Button>
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={submitReview}
                  className={reviewing.action === 'reject' ? 'bg-state-declining hover:bg-state-declining/90' : ''}
                >
                  {busy ? '提交中…' : '确认'}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
        </TabsContent>
        <TabsContent value="aliases" className="space-y-6">
          <SkillAliasesTable />
        </TabsContent>
      </Tabs>
    </div>
  )
}