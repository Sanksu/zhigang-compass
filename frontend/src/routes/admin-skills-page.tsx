import { useEffect, useMemo, useState } from 'react'
import { Check, Tag, X } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Reveal } from '@/components/ui/reveal'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { PaginationBar } from '@/components/ui/pagination'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { apiGet, apiPost } from '@/lib/api'
import { useSkillDescriptions } from '@/hooks/use-skill-descriptions'
import { formatDateTime } from '@/lib/utils'
import type { components } from '@/types/api'

type SkillAdminItem = components['schemas']['SkillAdminItem']
type SkillAliasItem = components['schemas']['SkillAliasItem']

const PAGE_SIZE = 20

const WHITELIST_OPTIONS = [
  { value: 'all', label: '全部白名单态' },
  { value: 'only', label: '仅白名单' },
  { value: 'exclude', label: '仅非白名单' },
] as const

const NOISE_OPTIONS = [
  { value: 'all', label: '全部噪声态' },
  { value: 'only', label: '仅噪声' },
  { value: 'exclude', label: '仅非噪声' },
] as const

const ALIAS_STATUS_LABEL: Record<string, string> = {
  pending: '待审',
  approved: '已生效',
  rejected: '已驳回',
}

const ALIAS_STATUS_TONE: Record<string, string> = {
  pending: 'text-state-candidate',
  approved: 'text-state-stable',
  rejected: 'text-state-declining',
}

/**
 * 技能治理页 — 管理端技能归一化治理统一入口。
 *
 *   Tab「技能总览」：白名单标准名 ∪ approved 别名标准名的聚合列表（只读），
 *     支持关键字/分类/白名单态/噪声态过滤与分页 —— 数据源 GET /admin/skills。
 *   Tab「别名复核」：别名回写记录（variant→standard）的人工处置 —— 列表
 *     GET /admin/skill-aliases，pending 行可 approve/reject
 *     POST /admin/skill-aliases/{id}/review（approved 即时写入动态别名表）。
 */
export function AdminSkillsPage() {
  const [tab, setTab] = useState<'overview' | 'aliases'>('overview')

  return (
    <>
      <PageHeader
        title="技能治理"
        description="技能归一化治理：白名单 ∪ 生效别名的聚合浏览与过滤，以及别名回写的复核处置（approve 即时写入动态别名表生效）"
      />
      <Reveal delay={380}>
        <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
          <TabsList className="mb-4">
            <TabsTrigger value="overview" className="text-xs">技能总览</TabsTrigger>
            <TabsTrigger value="aliases" className="text-xs">别名复核</TabsTrigger>
          </TabsList>
        </Tabs>
        {tab === 'overview' ? <SkillOverviewTab /> : <AliasReviewTab />}
      </Reveal>
    </>
  )
}

/** 技能总览：聚合列表 + q/分类/白名单/噪声过滤 */
function SkillOverviewTab() {
  const [items, setItems] = useState<SkillAdminItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [q, setQ] = useState('')
  const [category, setCategory] = useState('')
  const [whitelist, setWhitelist] = useState('all')
  const [noise, setNoise] = useState('all')
  const [debouncedQ, setDebouncedQ] = useState('')
  const [debouncedCategory, setDebouncedCategory] = useState('')

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQ(q.trim())
      setDebouncedCategory(category.trim())
    }, 300)
    return () => clearTimeout(timer)
  }, [q, category])

  const params = useMemo(() => {
    const p = new URLSearchParams({ page: String(page), size: String(PAGE_SIZE) })
    if (debouncedQ) p.set('q', debouncedQ)
    if (debouncedCategory) p.set('category', debouncedCategory)
    if (whitelist !== 'all') p.set('whitelist', whitelist)
    if (noise !== 'all') p.set('noise', noise)
    return p.toString()
  }, [debouncedQ, debouncedCategory, whitelist, noise, page])

  useEffect(() => {
    let cancelled = false
    // loading 置位在过滤/分页事件处理器中完成（lint：effect 内同步 setState
    // 触发级联渲染）；本 effect 只负责回落
    apiGet<{ items: SkillAdminItem[]; total: number; page: number; size: number }>(`/admin/skills?${params}`)
      .then((res) => {
        if (cancelled) return
        setError(null)
        setItems(res.items)
        setTotal(res.total)
      })
      .catch(() => {
        if (!cancelled) setError('技能列表加载失败，请稍后重试')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [params])

  // ── 技能解释：只读展示（编辑/补齐已移至「原始数据管理 → 技能治理」） ──
  const { descMap } = useSkillDescriptions()

  return (
    <Card>
      <CardContent className="pt-4">
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <Input
            value={q}
            onChange={(e) => {
              setQ(e.target.value)
              setPage(1)
              setLoading(true)
            }}
            placeholder="按标准名/别名过滤"
            className="w-56 h-8 text-sm"
          />
          <Input
            value={category}
            onChange={(e) => {
              setCategory(e.target.value)
              setPage(1)
              setLoading(true)
            }}
            placeholder="按分类过滤"
            className="w-44 h-8 text-sm"
          />
          <Select
            value={whitelist}
            onValueChange={(v) => {
              setWhitelist(v)
              setPage(1)
              setLoading(true)
            }}
          >
            <SelectTrigger aria-label="白名单过滤" className="h-8 w-full sm:w-36 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {WHITELIST_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={noise}
            onValueChange={(v) => {
              setNoise(v)
              setPage(1)
              setLoading(true)
            }}
          >
            <SelectTrigger aria-label="噪声过滤" className="h-8 w-full sm:w-36 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {NOISE_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="text-xs font-normal text-ink-faint">共 {total} 条</span>
        </div>

        {loading ? (
          <p className="py-12 text-center text-sm text-ink-muted">加载中…</p>
        ) : error ? (
          <p className="py-12 text-center text-sm text-state-archived">{error}</p>
        ) : items.length === 0 ? (
          <p className="py-12 text-center text-sm text-ink-faint">暂无技能记录</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>标准名</TableHead>
                <TableHead className="text-center">白名单</TableHead>
                <TableHead className="text-center">噪声</TableHead>
                <TableHead className="w-44">分类</TableHead>
                <TableHead>别名（生效）</TableHead>
                <TableHead className="w-64">技能解释</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((it) => (
                <TableRow key={it.name}>
                  <TableCell className="font-medium">{it.name}</TableCell>
                  <TableCell className="text-center">
                    {it.in_whitelist ? (
                      <Badge variant="outline" className="text-[11px] text-state-stable">白名单</Badge>
                    ) : (
                      <Badge variant="outline" className="text-[11px]">扩展</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-center">
                    {it.is_noise ? (
                      <Badge variant="archived" className="text-[11px]">噪声</Badge>
                    ) : (
                      <span className="text-xs text-ink-faint">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs text-ink-muted truncate">{it.category || '—'}</TableCell>
                  <TableCell className="max-w-60">
                    {it.aliases && it.aliases.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {it.aliases.slice(0, 6).map((a) => (
                          <Badge key={a} variant="outline" className="text-[11px] font-mono">{a}</Badge>
                        ))}
                        {it.aliases.length > 6 && (
                          <span className="text-[11px] text-ink-faint">+{it.aliases.length - 6}</span>
                        )}
                      </div>
                    ) : (
                      <span className="text-xs text-ink-faint">—</span>
                    )}
                  </TableCell>
                  <TableCell className="max-w-64">
                    <p
                      className="truncate text-xs text-ink-secondary"
                      title={descMap[it.name]?.override ?? descMap[it.name]?.builtin ?? undefined}
                    >
                      {descMap[it.name]?.override ?? descMap[it.name]?.builtin ?? '（空）'}
                    </p>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        <PaginationBar
          page={page}
          total={total}
          pageSize={PAGE_SIZE}
          loading={loading}
          onPageChange={(p) => { setPage(p); setLoading(true) }}
        />
      </CardContent>
    </Card>
  )
}

/** 别名复核：别名回写记录列表 + pending 行 approve/reject */
function AliasReviewTab() {
  const [items, setItems] = useState<SkillAliasItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('pending')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const [reloadKey, setReloadKey] = useState(0)

  const params = useMemo(() => {
    const p = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) })
    if (status) p.set('status', status)
    return p.toString()
  }, [status, page])

  useEffect(() => {
    let cancelled = false
    apiGet<{ items: SkillAliasItem[]; total: number; limit: number; offset: number }>(`/admin/skill-aliases?${params}`)
      .then((res) => {
        if (cancelled) return
        setError(null)
        setItems(res.items)
        setTotal(res.total)
      })
      .catch(() => {
        if (!cancelled) setError('别名列表加载失败，请稍后重试')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [params, reloadKey])

  async function review(item: SkillAliasItem, approved: boolean) {
    setBusyId(item.id)
    setActionError(null)
    try {
      await apiPost(`/admin/skill-aliases/${item.id}/review`, { approved })
      setLoading(true)
      setReloadKey((k) => k + 1)
    } catch {
      setActionError('复核失败，请稍后重试')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Card>
      <CardContent className="pt-4">
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <Select value={status} onValueChange={(v) => { setStatus(v); setPage(1); setLoading(true) }}>
            <SelectTrigger aria-label="状态过滤" className="h-8 w-full sm:w-36 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {['pending', 'approved', 'rejected'].map((s) => (
                <SelectItem key={s} value={s}>{ALIAS_STATUS_LABEL[s] ?? s}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="text-xs font-normal text-ink-faint">共 {total} 条</span>
        </div>

        {actionError && (
          <p className="mb-3 rounded-md border border-state-archived/30 bg-state-archived/5 px-3 py-2 text-xs text-state-archived">
            {actionError}
          </p>
        )}

        {loading ? (
          <p className="py-12 text-center text-sm text-ink-muted">加载中…</p>
        ) : error ? (
          <p className="py-12 text-center text-sm text-state-archived">{error}</p>
        ) : items.length === 0 ? (
          <p className="py-12 text-center text-sm text-ink-faint">
            {status === 'pending' ? '暂无待审别名——审批技能名归一（别名）提案后产生' : '暂无别名记录'}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>别名变体</TableHead>
                <TableHead>归并目标</TableHead>
                <TableHead className="text-center">置信度</TableHead>
                <TableHead className="text-center">状态</TableHead>
                <TableHead>审批理由</TableHead>
                <TableHead>创建时间</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((it) => (
                <TableRow key={it.id}>
                  <TableCell className="max-w-48 truncate font-medium"><Tag className="mr-1 inline size-3.5 text-ink-muted" />{it.variant}</TableCell>
                  <TableCell className="max-w-48 truncate">{it.standard_name}</TableCell>
                  <TableCell className="text-center font-mono text-xs text-ink-muted">
                    {it.confidence != null ? it.confidence.toFixed(2) : '—'}
                  </TableCell>
                  <TableCell className="text-center">
                    <Badge variant="outline" className={`text-[11px] ${ALIAS_STATUS_TONE[it.status] ?? ''}`}>
                      {ALIAS_STATUS_LABEL[it.status] ?? it.status}
                    </Badge>
                  </TableCell>
                  <TableCell className="max-w-52 truncate text-xs text-ink-muted">{it.review_reason || '—'}</TableCell>
                  <TableCell className="text-xs font-mono text-ink-muted">
                    {it.created_at ? formatDateTime(it.created_at) : '—'}
                  </TableCell>
                  <TableCell className="text-right">
                    {it.status === 'pending' ? (
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          className="text-state-stable"
                          disabled={busyId === it.id}
                          onClick={() => review(it, true)}
                        >
                          <Check className="mr-1 size-3.5" />
                          通过
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="text-state-archived"
                          disabled={busyId === it.id}
                          onClick={() => review(it, false)}
                        >
                          <X className="mr-1 size-3.5" />
                          驳回
                        </Button>
                      </div>
                    ) : (
                      <span className="text-xs text-ink-faint">已处置</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        <PaginationBar
          page={page}
          total={total}
          pageSize={PAGE_SIZE}
          loading={loading}
          onPageChange={(p) => { setPage(p); setLoading(true) }}
        />
      </CardContent>
    </Card>
  )
}