import { Fragment, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { ChevronDown, ChevronRight, ClipboardCheck, EyeOff, Gavel, ShieldAlert, ShieldCheck } from 'lucide-react'
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
  reverted: 'text-state-emerging',
}

const TIER_TONE: Record<string, string> = {
  R0: 'text-state-stable',
  R1: 'text-state-candidate',
  R2: 'text-state-declining',
}

/** 状态语义：该状态下决策是否已产生实际变更（展开详情首行展示） */
const STATUS_EXPLAIN: Record<string, string> = {
  shadow: '仅落决策记录，不产生任何生效（shadow 观察模式）',
  proposal: '待人工审批，尚未产生任何变更（操作列可批准/驳回）',
  auto_applied: '已由自动档执行（硬门通过 + 低影响面 + 高置信），实际副作用见下方「已执行的副作用」',
  blocked: '被硬门禁一票拦截，未执行任何变更',
  approved: '人工批准，变更已生效',
  rejected: '人工驳回，未产生任何变更',
  reverted: '曾自动生效，后经人工撤销——副作用已反做，同实体不再自动执行',
}

/** structured_output 常见键的中文名（未收录键原样展示） */
const OUTPUT_KEY_LABELS: Record<string, string> = {
  action: '动作',
  reason: '判断理由',
  impact: '影响面',
  kind: '类型',
  term: '词条',
  target_standard: '归一目标',
  canonical_name: '规范名',
  category: '建议分类',
  skill: '技能',
  sources: '来源',
  keep_original: '保留原名',
  is_new: '新岗位',
}

/** evidence_refs 条目键的中文名（按域异构：governance={label,value}、
 *  名称归一={source,source_url}、技能关系={kind,name,count,...}） */
const EVIDENCE_KEY_LABELS: Record<string, string> = {
  source: '来源',
  kind: '类型',
  name: '名称',
  count: '数量',
  position: '岗位',
  req_count: '引用数',
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function formatValue(v: unknown): string {
  if (v == null) return '-'
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  return JSON.stringify(v)
}

/** 动作影响说明：按域+动作给出「到底改了什么」的人类可读口径，
 *  与后端执行代码一一对应（dict_guard._apply_cleanup / 各域 approve 通道）。
 *  其余同类型记录据此获得同等详细说明，未收录组合回退为通用字段展示。 */
function effectExplain(it: LlmDecisionItem): string[] {
  const domain = it.domain ?? ''
  const action = str((it.structured_output ?? {})['action'])
  if (domain === 'governance') {
    if (action === 'add_stopword') {
      return [
        '写入动态停用词表（blocked）：后续技能归一化链路直接拦截该词',
        '删除图谱同名 Skill 节点及其全部关系边',
      ]
    }
    if (action === 'remove_node') {
      const label = it.entity_type === 'position' ? '岗位 Position' : '课程 Course'
      return [`删除图谱 ${label} 节点（DETACH DELETE，连带其全部关系边）`]
    }
    if (action === 'remove_edge') {
      return ['删除课程脏边 Skill-[:LEARNABLE_VIA]->Course（词条为「技能→课程」格式）']
    }
    if (action === 'hide_node') return ['隐藏图谱节点（数据保留，前端不再展示）']
    if (action === 'add_blocked') return ['写入动态黑名单，抽取/归一链路拦截该词条目']
    return []
  }
  if (domain === 'skill_relation') return ['批准后按关系类型在图谱建边或调整关系（源技能→目标技能→类型）']
  if (domain === 'position_normalize') return ['批准后执行岗位名归一（别名回写/节点归并），归一产物落图']
  if (domain === 'skill_normalize') {
    return [
      '批准后执行技能名归一：kind=alias 回写别名词典，归并类在图谱合并节点',
    ]
  }
  if (domain === 'skill_classify') return ['批准后该分类晋升为权威 category，后续抽取/展示按此归类']
  if (domain === 'position_classify') return ['批准后岗位归类落图']
  if (domain === 'cluster_label') return ['批准后簇标签写入图谱，用于图谱分组展示']
  if (domain === 'jd_extract') return ['抽取类决策仅记录 LLM 结构化输出与置信度，无独立生效动作']
  return []
}

/** 影响说明小节标题：按当前状态区分「已发生 / 将发生 / 已反做」 */
function effectTitle(status: string | undefined): string {
  if (status === 'auto_applied' || status === 'approved') return '已执行的副作用'
  if (status === 'proposal') return '批准后将执行'
  if (status === 'reverted') return '被撤销的副作用（已反做）'
  return '动作影响（当前未生效）'
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
  const navigate = useNavigate()

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
  // 卡片=真实可操作口径：治理（governance）提案不在本页审批（队列在字典治理页
  // dict_proposals），单列并给跳转；「待审提案」只计本页有批准/驳回按钮的域
  const byDomain = summary?.by_domain ?? []
  const statusOf = (d: string) => byDomain.find((x) => x.domain === d)?.by_status ?? {}
  const mutablePending =
    (['skill_relation', 'position_normalize', 'skill_normalize'] as const).reduce(
      (n, d) => n + (statusOf(d)['proposal'] ?? 0),
      0,
    ) + (statusOf('skill_classify')['shadow'] ?? 0)
  const govPending = statusOf('governance')['proposal'] ?? 0

  const cards: { label: string; value: number; tone: string; icon: typeof ShieldCheck; filter?: { domain?: string; status?: string } }[] = [
    { label: '待审提案（本页可批驳）', value: mutablePending, tone: 'text-state-candidate', icon: ClipboardCheck, filter: { status: 'proposal' } },
    { label: '治理提案（字典治理页处理）', value: govPending, tone: 'text-state-declining', icon: Gavel, filter: { domain: 'governance', status: 'proposal' } },
    { label: '自动生效', value: totals?.auto_applied ?? 0, tone: 'text-state-stable', icon: ShieldCheck, filter: { status: 'auto_applied' } },
    { label: '硬门拦截', value: totals?.blocked ?? 0, tone: 'text-state-declining', icon: ShieldAlert, filter: { status: 'blocked' } },
    { label: 'Shadow 记录', value: totals?.shadow ?? 0, tone: 'text-state-emerging', icon: EyeOff, filter: { status: 'shadow' } },
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

  // 治理救济：governance 域 auto_applied 且动作为可反做集合 → 可撤销（7 天窗口由后端校验）
  const UNDOABLE_ACTIONS = ['add_stopword', 'remove_node', 'remove_edge']
  function undoableAction(it: LlmDecisionItem): boolean {
    if (it.domain !== 'governance' || it.status !== 'auto_applied') return false
    const action = str((it.structured_output ?? {})['action'])
    return UNDOABLE_ACTIONS.includes(action)
  }

  /** 名称归一域的审批关键信息（与后端 parse_normalization 同口径，人工凭此判断批什么）：
   *  position_normalize：keep_original→确认原样（批准无图变更）；is_new→改名到新标准名；否则并入已有标准名。
   *  skill_normalize：kind=alias→别名回写；action=merge→归并。 */
  function normalizationTarget(it: LlmDecisionItem): { target: string; badge: string } | null {
    const out = (it.structured_output ?? {}) as Record<string, unknown>
    if (it.domain === 'skill_normalize') {
      const target = str(out['target_standard']) || str(out['canonical_name'])
      if (!target) return null
      return { target, badge: out['kind'] === 'alias' ? '别名' : '归并' }
    }
    if (it.domain === 'position_normalize') {
      if (out['keep_original'] === true) return { target: '', badge: '确认原样' }
      const target = str(out['canonical_name'])
      if (!target) return null
      return { target, badge: out['is_new'] === true ? '改名' : '并入' }
    }
    return null
  }

  /** 行内展开详情：多开（Set），便于并排比对多条记录 */
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  function toggleExpand(id: string) {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const [reviewing, setReviewing] = useState<{ id: string; entity: string; action: 'approve' | 'reject' | 'undo' } | null>(null)
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
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-5">
        {cards.map((c) => (
          <Card
            key={c.label}
            className={c.filter ? 'cursor-pointer transition-colors hover:border-ink-secondary/40' : undefined}
            onClick={c.filter ? () => applyFilter(c.filter!) : undefined}
            title={c.filter ? '点击按此状态过滤列表' : undefined}
          >
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
                {['shadow', 'proposal', 'auto_applied', 'blocked', 'approved', 'rejected', 'reverted'].map((s) => (
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
                  <TableHead className="w-8" aria-label="展开详情" />
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
                    <TableCell colSpan={9} className="py-8 text-center text-muted-foreground">
                      加载中…
                    </TableCell>
                  </TableRow>
                ) : filtered.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={9} className="py-8 text-center text-muted-foreground">
                      无匹配的决策记录
                    </TableCell>
                  </TableRow>
                ) : (
                  filtered.map((it) => {
                    const isOpen = expandedIds.has(it.id)
                    const out = (it.structured_output ?? {}) as Record<string, unknown>
                    const kvPairs = Object.entries(out)
                    const evidence = Array.isArray(it.evidence_refs) ? it.evidence_refs : []
                    const effects = effectExplain(it)
                    return (
                      <Fragment key={it.id}>
                        <TableRow className={isOpen ? 'bg-muted/30' : undefined}>
                          <TableCell>
                            <button
                              type="button"
                              aria-expanded={isOpen}
                              aria-label={isOpen ? '收起详情' : '展开详情'}
                              className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                              onClick={() => toggleExpand(it.id)}
                            >
                              {isOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                            </button>
                          </TableCell>
                          <TableCell>{DOMAIN_LABELS[it.domain ?? ""] ?? it.domain ?? "-"}</TableCell>
                      <TableCell className="max-w-[260px]">
                        <div className="flex flex-col">
                          <span className="truncate">
                            {it.entity_type ? `${it.entity_type}:` : ''}
                            {it.entity_id || '-'}
                          </span>
                          {(() => {
                            const sug = normalizationTarget(it)
                            if (!sug) return null
                            return (
                              <span className="flex items-center gap-1 truncate text-xs text-muted-foreground">
                                {sug.target && (
                                  <>
                                    <span className="text-state-candidate">→</span>
                                    <span className="truncate">{sug.target}</span>
                                  </>
                                )}
                                <Badge variant="outline" className="h-4 shrink-0 px-1 text-[10px] text-state-emerging">
                                  {sug.badge}
                                </Badge>
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
                        ) : it.domain === 'governance' && it.status === 'proposal' ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 text-xs text-state-candidate"
                            onClick={() => navigate('/admin/review/dict')}
                          >
                            字典治理审核
                          </Button>
                        ) : undoableAction(it) ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 text-xs text-state-declining"
                            onClick={() => {
                              setActionError(null)
                              setReviewing({ id: it.id, entity: `${it.entity_type ?? ''}:${it.entity_id ?? ''}`, action: 'undo' })
                            }}
                          >
                            撤销
                          </Button>
                        ) : (
                          <span className="text-xs text-muted-foreground">-</span>
                        )}
                      </TableCell>
                        </TableRow>
                        {isOpen && (
                          <TableRow>
                            <TableCell colSpan={9} className="bg-muted/20 px-12 py-3">
                              <div className="max-w-4xl space-y-2.5 text-xs leading-relaxed">
                                <p>
                                  <span className="mr-2 font-medium text-foreground">状态说明</span>
                                  <span className="text-muted-foreground">
                                    {STATUS_EXPLAIN[it.status ?? ''] ?? '—'}
                                  </span>
                                </p>
                                {effects.length > 0 && (
                                  <div>
                                    <p className="font-medium text-foreground">{effectTitle(it.status)}</p>
                                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-muted-foreground">
                                      {effects.map((line) => (
                                        <li key={line}>{line}</li>
                                      ))}
                                    </ul>
                                  </div>
                                )}
                                {kvPairs.length > 0 && (
                                  <div>
                                    <p className="font-medium text-foreground">决策输出（structured_output）</p>
                                    <div className="mt-1 space-y-0.5">
                                      {kvPairs.map(([k, v]) => (
                                        <p key={k} className="text-muted-foreground">
                                          <span className="mr-2 inline-block min-w-24 font-medium text-foreground/80">
                                            {OUTPUT_KEY_LABELS[k] ?? k}
                                          </span>
                                          <span className="break-all">{formatValue(v)}</span>
                                        </p>
                                      ))}
                                    </div>
                                  </div>
                                )}
                                {evidence.length > 0 && (
                                  <div>
                                    <p className="font-medium text-foreground">证据引用</p>
                                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-muted-foreground">
                                      {evidence.map((ev, i) => {
                                        const item = (ev ?? {}) as Record<string, unknown>
                                        // 证据条目按域异构（键形态见 EVIDENCE_KEY_LABELS）：
                                        // 逐键通用渲染，带 source_url 的渲染成可点击原文链接
                                        const pairs = Object.entries(item).filter(([k]) => k !== 'source_url')
                                        return (
                                          <li key={i} className="break-all">
                                            {pairs.length > 0
                                              ? pairs
                                                  .map(([k, v]) => `${EVIDENCE_KEY_LABELS[k] ?? k}：${formatValue(v)}`)
                                                  .join('　')
                                              : formatValue(item)}
                                            {typeof item['source_url'] === 'string' && (
                                              <>
                                                {' '}
                                                <a
                                                  href={item['source_url'] as string}
                                                  target="_blank"
                                                  rel="noreferrer"
                                                  className="underline hover:no-underline"
                                                >
                                                  查看原文
                                                </a>
                                              </>
                                            )}
                                          </li>
                                        )
                                      })}
                                    </ul>
                                  </div>
                                )}
                                <p className="text-muted-foreground">
                                  运行 <span className="font-mono">{it.run_id || '-'}</span>
                                  {' · '}{it.provider || '-'}/{it.model || '-'}
                                  {' · '}硬门 {it.gate_result || '-'}
                                  {it.rollback_ref ? ` · 回滚引用 ${it.rollback_ref}` : ''}
                                </p>
                                {it.reviewer && (
                                  <p className="text-muted-foreground">
                                    审核人 {it.reviewer}：{it.review_reason}
                                  </p>
                                )}
                              </div>
                            </TableCell>
                          </TableRow>
                        )}
                      </Fragment>
                    )
                  })
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
                  const sug = normalizationTarget(it)
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
                            {sug.target && (
                              <>
                                <span className="text-state-candidate">→</span>
                                <span className="truncate">{sug.target}</span>
                              </>
                            )}
                            <Badge variant="outline" className="h-4 shrink-0 px-1 text-[10px] text-state-emerging">
                              {sug.badge}
                            </Badge>
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
                        ) : it.domain === 'governance' && it.status === 'proposal' ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 text-xs text-state-candidate"
                            onClick={() => navigate('/admin/review/dict')}
                          >
                            字典治理审核
                          </Button>
                        ) : undoableAction(it) ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 text-xs text-state-declining"
                            onClick={() => {
                              setActionError(null)
                              setReviewing({ id: it.id, entity: `${it.entity_type ?? ''}:${it.entity_id ?? ''}`, action: 'undo' })
                            }}
                          >
                            撤销
                          </Button>
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
              <CardTitle>
                {reviewing.action === 'approve' ? '批准' : reviewing.action === 'undo' ? '撤销自动生效' : '驳回'}决策记录
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">
                {reviewing.action === 'approve'
                  ? '批准后'
                  : reviewing.action === 'undo'
                    ? '撤销将反做治理副作用（移除动态过滤 / 重建课程节点），同实体不再自动执行：'
                    : '驳回将仅流转状态'}:{' '}
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