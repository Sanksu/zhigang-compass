import { useEffect, useMemo, useState } from 'react'
import { ClipboardCheck, EyeOff, ShieldAlert, ShieldCheck } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { PaginationBar } from '@/components/ui/pagination'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { apiGet } from '@/lib/api'
import { formatDateTime } from '@/lib/utils'
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
  const [summary, setSummary] = useState<{ by_domain: DomainSummary[]; totals: Record<string, number> } | null>(null)
  const [page, setPage] = useState(1)
  const [domain, setDomain] = useState('')
  const [status, setStatus] = useState('')
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const params = useMemo(() => {
    const p = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) })
    if (domain) p.set('domain', domain)
    if (status) p.set('status', status)
    return p.toString()
  }, [domain, status, page])

  useEffect(() => {
    let cancelled = false
    apiGet<{ items: LlmDecisionItem[]; limit: number; offset: number }>(`/admin/llm-decisions?${params}`)
      .then((res) => {
        if (cancelled) return
        setError(null)
        setItems(res.items)
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

  return (
    <div className="space-y-6">
      <PageHeader
        title="LLM 决策与验收"
        description="六域 LLM 决策统一透明：JD 抽取 / 名称归一 / 分类 / 治理 / 技能关系 —— shadow·提案·自动生效·硬门拦截全部可追溯可回放"
      />

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
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <CardTitle>决策记录</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              placeholder="搜索 entity / run_id"
              className="h-8 w-52"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <select
              aria-label="域过滤"
              className="h-8 rounded-md border bg-background px-2 text-sm"
              value={domain}
              onChange={(e) => applyFilter({ domain: e.target.value })}
            >
              <option value="">全部域</option>
              {Object.entries(DOMAIN_LABELS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
            <select
              aria-label="状态过滤"
              className="h-8 rounded-md border bg-background px-2 text-sm"
              value={status}
              onChange={(e) => applyFilter({ status: e.target.value })}
            >
              <option value="">全部状态</option>
              {['shadow', 'proposal', 'auto_applied', 'blocked', 'approved', 'rejected'].map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <Button variant="ghost" size="sm" onClick={() => setQ('')}>
              清除
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {error ? (
            <p className="py-8 text-center text-sm text-state-declining">{error}</p>
          ) : (
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
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading && filtered.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                      加载中…
                    </TableCell>
                  </TableRow>
                ) : filtered.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                      无匹配的决策记录
                    </TableCell>
                  </TableRow>
                ) : (
                  filtered.map((it) => (
                    <TableRow key={it.id}>
                      <TableCell>{DOMAIN_LABELS[it.domain ?? ""] ?? it.domain ?? "-"}</TableCell>
                      <TableCell className="max-w-[220px] truncate">
                        {it.entity_type ? `${it.entity_type}:` : ''}
                        {it.entity_id || '-'}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className={`text-[10px] ${STATUS_TONE[it.status ?? ''] ?? ''}`}>
                          {it.status}
                        </Badge>
                      </TableCell>
                      <TableCell className={`text-xs font-semibold ${TIER_TONE[it.risk_tier ?? ''] ?? ''}`}>
                        {it.risk_tier || '-'}
                      </TableCell>
                      <TableCell>{it.confidence != null ? it.confidence.toFixed(2) : '-'}</TableCell>
                      <TableCell className="text-xs">{it.provider || '-'}</TableCell>
                      <TableCell className="text-xs">{it.created_at ? formatDateTime(it.created_at) : '-'}</TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          )}
          <PaginationBar
            page={page}
            pageSize={PAGE_SIZE}
            total={Math.max(filtered.length, page * PAGE_SIZE)}
            onPageChange={setPage}
          />
        </CardContent>
      </Card>
    </div>
  )
}