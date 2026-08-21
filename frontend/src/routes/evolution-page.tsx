/**
 * 演化看板页 — 设计文档 §7 动态演化与新岗位发现
 *
 * 数据来源：真实后端 API
 * - GET /api/v1/evolution/versions → 版本列表（顶部指标 + diff 下拉）
 * - GET /api/v1/evolution/versions/{id} → 版本详情弹窗
 * - GET /api/v1/evolution/diff      → 版本快照差异
 * - GET /api/v1/evolution/trends    → 技能频次趋势
 * - GET /api/v1/evolution/signals   → 新兴/衰退信号
 * - GET /api/v1/evolution/position/{id}/evolution → 岗位演化历史
 * - GET /api/v1/evolution/state-machine → 岗位状态机流转（六态分布 + 人工审核记录）
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Calendar, GitBranch, TrendingUp, TrendingDown, Eye, Boxes, Play, Pause } from 'lucide-react'
import * as echarts from 'echarts'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge, type BadgeProps } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { PaginationBar } from '@/components/ui/pagination'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import { cn, isDark } from '@/lib/utils'
import {apiGet, errMsg} from '@/lib/api'
import type { components } from '@/types/api'

// ===== Types =====

type TrendTone = 'emerging' | 'declining' | 'stable'

interface VersionDiffItem {
  id: string
  name: string
  type: 'position' | 'skill' | 'evidence' | 'course' | 'tool'
  change: 'added' | 'removed' | 'changed'
  detail: string
}

interface MetricItem {
  key: string
  label: string
  value: string | number
  delta: number
  tone: TrendTone
  hint: string
}

/** 后端 /evolution/versions 返回项 */
type EvolutionVersion = components['schemas']['EvolutionVersion']

/** 后端 /evolution/diff 返回的节点项（含真实名称） */
type EvolutionDiffNode = components['schemas']['EvolutionDiffNode']

/** 后端 /evolution/diff 返回项 */
type EvolutionDiff = components['schemas']['EvolutionDiff']


/** 后端 /evolution/signals 返回项（EvolutionSignal 序列化） */
type EvolutionSignal = components['schemas']['EvolutionSignal']

type EvolutionSignalsData = components['schemas']['EvolutionSignalsData']

/** 后端 /evolution/versions/{id} 返回的版本详情 */
type EvolutionVersionDetail = components['schemas']['EvolutionVersionDetail']

/** 后端 /evolution/position/{id}/evolution 返回项 */
type PositionEvolutionData = components['schemas']['PositionEvolutionData']

/** 后端 /evolution/positions 返回项（默认岗位演化列表） */
type PositionEvolutionListData = components['schemas']['PositionEvolutionListData']

/** 后端 /evolution/events 返回的谱系事件项 */
type EvolutionEvent = components['schemas']['EvolutionEvent']

/** 后端 /evolution/events 返回项 */
type EvolutionEventListData = components['schemas']['EvolutionEventListData']

/** 后端 /evolution/skills 返回项 */
type SkillEvolutionListData = components['schemas']['SkillEvolutionListData']

/** 后端 /evolution/skills 列表项（含快照点） */
type SkillEvolutionData = components['schemas']['SkillEvolutionData']

// ===== SignalsView =====

/** 新兴/衰退技能 Top-N（真实 GET /evolution/signals） */
function SignalsView() {
  const [data, setData] = useState<EvolutionSignalsData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiGet<EvolutionSignalsData>('/evolution/signals?top_n=10')
      .then(setData)
      .catch((e) => setError(errMsg(e, '信号加载失败')))
  }, [])

  if (error) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-xs text-state-archived">{error}</CardContent>
      </Card>
    )
  }
  if (!data) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-xs text-ink-muted">加载演化信号…</CardContent>
      </Card>
    )
  }

  function renderList(items: EvolutionSignal[], tone: 'emerging' | 'declining', windowCount: number) {
    if (items.length === 0) {
      return (
        <p className="py-6 text-center text-xs text-ink-faint">
          {windowCount < 2
            ? `历史快照不足（当前 ${windowCount} 期，需 ≥2 期），冷启动阶段暂不判定`
            : '本期无该趋势信号'}
        </p>
      )
    }
    const toneColor = tone === 'emerging' ? 'text-state-emerging' : 'text-state-declining'
    return (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8">#</TableHead>
            <TableHead>技能</TableHead>
            <TableHead className="text-right">Z-score</TableHead>
            <TableHead className="text-right">当期频次</TableHead>
            <TableHead className="text-right">占比口径</TableHead>
            <TableHead className="text-right">置信度</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((s, i) => (
            <TableRow key={s.skill_id}>
              <TableCell className="text-xs font-mono text-ink-faint">{i + 1}</TableCell>
              <TableCell className="font-medium text-ink">
                {s.skill_name}
                {s.warning && (
                  <span
                    title="证据量异常期（样本量对比告警命中），信号读数受采集波动影响，谨慎解读"
                    className="ml-1.5 inline-flex items-center rounded-sm border border-state-declining/40 bg-state-declining/10 px-1 text-[10px] font-normal text-state-declining"
                  >
                    ⚠ 证据量异常
                  </span>
                )}
              </TableCell>
              <TableCell className={cn('text-right font-mono tabular-nums', toneColor)}>
                {s.z_score != null ? s.z_score.toFixed(2) : '—'}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums text-ink-secondary">{s.current_freq}</TableCell>
              <TableCell className="text-right font-mono tabular-nums text-ink-faint">
                {s.freq_ratio != null ? `${(s.freq_ratio * 100).toFixed(1)}%` : '—'}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums text-ink-muted">
                {(s.confidence * 100).toFixed(0)}%
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    )
  }

  return (
    <div className="mb-4">
      {(data.warnings?.length ?? 0) > 0 && (
        <Card className="mb-4 border-state-declining/30 bg-state-declining/5">
          <CardContent className="py-3 text-xs text-state-declining">
            ⚠ 采样窗口内 {data.warnings!.length} 个图谱版本命中样本量对比告警
            （证据量萎缩 &lt;50% 或膨胀 &gt;200%，见版本列表）；信号已打「证据量异常」标，
            判定口径不受影响，解读时请注意采集波动。
          </CardContent>
        </Card>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              <TrendingUp className="size-4 text-state-emerging" />
              <span>新兴技能 Top-10</span>
              <span className="text-[10px] font-normal text-ink-faint">z &gt; 2.0</span>
            </CardTitle>
          </CardHeader>
          <CardContent>{renderList(data.emerging, 'emerging', data.window_count)}</CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              <TrendingDown className="size-4 text-state-declining" />
              <span>衰退技能 Top-10</span>
              <span className="text-[10px] font-normal text-ink-faint">z &lt; -1.5</span>
            </CardTitle>
          </CardHeader>
          <CardContent>{renderList(data.declining, 'declining', data.window_count)}</CardContent>
        </Card>
      </div>
    </div>
  )
}

// ===== SkillDeclineWarningCard =====

/** C 端技能衰退预警摘要卡（风险治理引导）：declining Top-N 一眼可见。

 * 数据复用 /evolution/signals（Redis 缓存 60s），衰退技能以橙徽标 + Z-score
 * 悬浮提示呈现；无信号不渲染（不留占位）。 */
function SkillDeclineWarningCard() {
  const [declining, setDeclining] = useState<EvolutionSignal[] | null>(null)

  useEffect(() => {
    let cancelled = false
    apiGet<EvolutionSignalsData>('/evolution/signals?top_n=8')
      .then((r) => {
        if (!cancelled) setDeclining(r.declining)
      })
      .catch(() => {
        if (!cancelled) setDeclining([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (!declining || declining.length === 0) return null
  return (
    <Card className="mb-4 border-state-declining/30 bg-state-declining/5">
      <CardHeader className="pb-1">
        <CardTitle className="flex items-center gap-2 text-sm text-state-declining">
          <TrendingDown className="size-4" />
          <span>技能衰退预警 · {declining.length} 项</span>
          <span className="text-[10px] font-normal text-ink-faint">
            以下技能需求呈衰退信号（Z &lt; -1.5），求职者请关注学习路径中的替代技能
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-wrap gap-1.5">
        {declining.map((s) => (
          <Badge
            key={s.skill_id}
            variant="outline"
            className="bg-state-declining/10 text-[11px] text-state-declining"
            title={
              s.warning
                ? `${s.skill_name}：Z=${s.z_score?.toFixed(2) ?? '—'}（证据量异常期，谨慎解读）`
                : `${s.skill_name}：Z=${s.z_score?.toFixed(2) ?? '—'} · 频次 ${s.current_freq}`
            }
          >
            {s.skill_name}
          </Badge>
        ))}
      </CardContent>
    </Card>
  )
}

// ===== TechnologyWatchView =====

/** 技术热点观察池（真实 GET /evolution/watch，MLI 产业化拐点排名） */
type WatchItem = components['schemas']['WatchOverviewItem']

const SOURCE_LABEL: Record<string, string> = {
  jd: 'JD',
  arxiv: '论文',
  course: '课程',
  github: 'GitHub',
  community: '社区',
  stackoverflow: 'SO',
}

/** 可搜索下拉（08-16：岗位/技能/版本全量可搜索选择）
 *
 * options 为当前可选项；输入时先本地过滤，若提供 onSearch 则由父组件
 * 防抖拉取后端匹配（positions/skills 走 q 参数；versions 仅本地过滤）。
 */
function SearchableSelect({
  value,
  placeholder,
  options,
  loading,
  onSearch,
  onSelect,
  pageSize,
}: {
  value: string
  placeholder: string
  options: { value: string; label: string }[]
  loading?: boolean
  onSearch?: (q: string) => void
  onSelect: (v: string) => void
  /** 选项分页（10 项一页，08-16 用户决策：版本对比下拉翻页浏览） */
  pageSize?: number
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [optionPage, setOptionPage] = useState(1)
  const timerRef = useRef<number | null>(null)
  const current = options.find((o) => o.value === value)
  const ql = q.trim().toLowerCase()
  const filtered = ql
    ? options.filter((o) => o.label.toLowerCase().includes(ql))
    : options
  const totalPages = pageSize ? Math.max(1, Math.ceil(filtered.length / pageSize)) : 1
  const visible = pageSize
    ? filtered.slice((optionPage - 1) * pageSize, optionPage * pageSize)
    : filtered

  return (
    <div className="relative">
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-8 w-48 justify-start font-mono text-xs"
        onClick={() => {
          setOpen((v) => !v)
          setQ('')
          setOptionPage(1)
        }}
      >
        <span className="truncate text-ink">{current?.label || placeholder}</span>
      </Button>
      {open && (
        <>
          {/* 点击外部关闭 */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute z-50 mt-1 w-64 rounded-md border border-border bg-elevated shadow-lg">
            <div className="p-1.5">
              <Input
                autoFocus
                value={q}
                placeholder="输入名称搜索…"
                className="h-7 text-xs"
                onChange={(e) => {
                  const v = e.target.value
                  setQ(v)
                  setOptionPage(1)
                  if (!onSearch) return
                  if (timerRef.current) window.clearTimeout(timerRef.current)
                  timerRef.current = window.setTimeout(() => onSearch(v.trim()), 300)
                }}
              />
            </div>
            <div className="max-h-64 overflow-auto p-1">
              {loading ? (
                <p className="px-2 py-3 text-center text-xs text-ink-faint">搜索中…</p>
              ) : visible.length === 0 ? (
                <p className="px-2 py-3 text-center text-xs text-ink-faint">无匹配结果</p>
              ) : (
                visible.map((o) => (
                  <button
                    key={o.value}
                    type="button"
                    className={cn(
                      'block w-full truncate rounded px-2 py-1.5 text-left text-xs hover:bg-subtle',
                      o.value === value && 'bg-subtle font-medium',
                    )}
                    onClick={() => {
                      onSelect(o.value)
                      setOpen(false)
                    }}
                  >
                    {o.label}
                  </button>
                ))
              )}
            </div>
            {/* 选项分页（10 项一页，08-16 用户决策） */}
            {pageSize && totalPages > 1 && (
              <div className="flex items-center justify-between border-t border-border px-2 py-1.5">
                <span className="text-[10px] text-ink-faint">
                  第 {Math.min(optionPage, totalPages)} / {totalPages} 页 · 共 {filtered.length} 个
                </span>
                <div className="flex items-center gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 px-2 text-[10px]"
                    disabled={optionPage <= 1}
                    onClick={() => setOptionPage((p) => Math.max(1, p - 1))}
                  >
                    上一页
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 px-2 text-[10px]"
                    disabled={optionPage >= totalPages}
                    onClick={() => setOptionPage((p) => Math.min(totalPages, p + 1))}
                  >
                    下一页
                  </Button>
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function TechnologyWatchView() {
  const [data, setData] = useState<WatchItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 08-16 翻页：观察池 10 项一页（GET /evolution/watch page/size）
  const PAGE_SIZE = 10
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [pageLoading, setPageLoading] = useState(false)

  // 请求共享：loadPage 与初始 effect 复用同一 fetch（08-17 收敛重复请求）
  function fetchWatchPage(p: number) {
    return apiGet<components['schemas']['WatchOverviewData']>(`/evolution/watch?page=${p}&size=${PAGE_SIZE}`)
  }

  function loadPage(p: number) {
    setPageLoading(true)
    fetchWatchPage(p)
      .then((r) => {
        setData(r.items)
        setTotal(r.total)
      })
      .catch((e) => setError(errMsg(e, '技术热点加载失败')))
      .finally(() => setPageLoading(false))
  }

  // 初始加载：setState 均在请求回调（异步）中，规避 effect 同步 setState 规则
  useEffect(() => {
    let cancelled = false
    fetchWatchPage(1)
      .then((r) => {
        if (cancelled) return
        setData(r.items)
        setTotal(r.total)
      })
      .catch((e) => {
        if (!cancelled) setError(errMsg(e, '技术热点加载失败'))
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (error) {
    return (
      <Card className="mb-4">
        <CardContent className="py-8 text-center text-xs text-state-archived">{error}</CardContent>
      </Card>
    )
  }

  return (
    <Card className="mb-4">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Eye className="size-4 text-ink" />
          <span>技术热点观察池</span>
          <span className="text-[10px] font-normal text-ink-faint">MLI 产业化拐点 · 设计文档 §7.2.5</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {data === null ? (
          <p className="py-6 text-center text-xs text-ink-faint">加载观察池…</p>
        ) : data.length === 0 ? (
          <p className="py-6 text-center text-xs text-ink-faint">暂无技术热点信号（依赖每日观察池任务）</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>技能</TableHead>
                <TableHead>MLI 指数</TableHead>
                <TableHead>信号来源</TableHead>
                <TableHead>产业化</TableHead>
                <TableHead>状态</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((w) => (
                <TableRow key={w.skill_name}>
                  <TableCell className="font-medium text-ink">{w.skill_name}</TableCell>
                  <TableCell className="font-mono tabular-nums text-ink-muted">
                    {w.mli.toFixed(2)}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {w.sources.map((s) => (
                        <Badge key={s} variant="outline" className="text-[10px] font-mono">
                          {SOURCE_LABEL[s] ?? s}
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell>
                    {w.ready_to_industrialize ? (
                      <Badge className="text-[10px] bg-state-emerging">可产业化</Badge>
                    ) : (
                      <Badge variant="outline" className="text-[10px]">观察中</Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant={w.status === 'candidate_promoted' ? 'emerging' : 'outline'} className="text-[10px]">
                      {w.status === 'candidate_promoted' ? '候选提升' : w.status === 'archived' ? '归档' : '观察'}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {/* 观察池翻页（10 项一页，08-16） */}
        {data && data.length > 0 && (
          <PaginationBar
            page={page}
            total={total}
            pageSize={PAGE_SIZE}
            loading={pageLoading}
            onPageChange={(p) => {
              setPage(p)
              loadPage(p)
            }}
          />
        )}
      </CardContent>
    </Card>
  )
}

// ===== SnapshotTimelineView =====

/** 快照时间线通用视图（08-17：SkillTrendView/PositionEvolutionView 孪生组件收敛）。

 * 两者共享：默认列表加载 + 可搜索下拉 + 手动 ID 查询 + 快照时间线
 * 10 期/页翻页（最新在前）；差异（端点/字段/表格列）经配置参数化。
 */
interface SnapshotPoint {
  date?: string | null
  version?: string
  freq?: number
  present?: boolean
}

function SnapshotTimelineView<T extends { points?: SnapshotPoint[] }>({
  icon: Icon,
  title,
  subtitle,
  selectPlaceholder,
  idPlaceholder,
  idErrorMsg,
  defaultErrorMsg,
  loadErrorMsg,
  noDataMsg,
  emptyMsg,
  loadingMsg,
  listUrl,
  searchUrl,
  detailUrl,
  idOf,
  nameOf,
  extractList,
  extractPoints,
  freqLabel,
  extraColumns,
}: {
  icon: typeof TrendingUp
  title: string
  subtitle?: string
  selectPlaceholder: string
  idPlaceholder: string
  idErrorMsg: string
  defaultErrorMsg: string
  loadErrorMsg: string
  noDataMsg: string
  emptyMsg: string
  loadingMsg: string
  listUrl: string
  searchUrl: (q: string) => string
  detailUrl: (id: string) => string
  idOf: (d: T) => string
  nameOf: (d: T) => string
  extractList: (r: unknown) => T[]
  extractPoints: (d: T) => SnapshotPoint[]
  freqLabel: string
  extraColumns?: (d: T, p: SnapshotPoint) => ReactNode
}) {
  const [idInput, setIdInput] = useState('')
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [defaults, setDefaults] = useState<T[] | null>(null)
  const [defaultError, setDefaultError] = useState<string | null>(null)
  // 08-16 用户决策：翻页针对快照时间线（10 期/页、最新在前），列表不翻页
  const SNAPSHOT_PAGE_SIZE = 10
  const [snapshotPage, setSnapshotPage] = useState(1)
  const [searchLoading, setSearchLoading] = useState(false)

  // 配置经 ref 传递，effect 仅首挂载执行（避免内联回调导致的重复请求）；
  // ref 更新放 effect（render 中写 ref 违反 react-hooks/refs）
  const cfgRef = useRef({ searchUrl, detailUrl, defaultErrorMsg, loadErrorMsg, idErrorMsg, listUrl, extractList })
  useEffect(() => {
    cfgRef.current = { searchUrl, detailUrl, defaultErrorMsg, loadErrorMsg, idErrorMsg, listUrl, extractList }
  })

  function search(q: string) {
    setSearchLoading(true)
    apiGet(cfgRef.current.searchUrl(q))
      .then((r) => setDefaults(cfgRef.current.extractList(r)))
      .catch(() => setDefaultError(cfgRef.current.defaultErrorMsg))
      .finally(() => setSearchLoading(false))
  }

  // 页面加载即拉取 Top 列表（GET listUrl），默认选中首项
  useEffect(() => {
    let cancelled = false
    apiGet(cfgRef.current.listUrl)
      .then((r) => {
        if (cancelled) return
        const list = cfgRef.current.extractList(r)
        setDefaults(list)
        if (list.length > 0) setData(list[0])
      })
      .catch((e) => {
        if (!cancelled) setDefaultError(errMsg(e, cfgRef.current.defaultErrorMsg))
      })
    return () => {
      cancelled = true
    }
  }, [])

  function load() {
    const id = idInput.trim()
    if (!id) {
      setError(cfgRef.current.idErrorMsg)
      return
    }
    setLoading(true)
    setError(null)
    apiGet(cfgRef.current.detailUrl(id))
      .then((r) => {
        setData(r as T)
        setSnapshotPage(1)
      })
      .catch((e) => {
        setData(null)
        setError(errMsg(e, cfgRef.current.loadErrorMsg))
      })
      .finally(() => setLoading(false))
  }

  // 快照时间线：日期最新在前，按 10 期/页切片（08-16 用户决策）
  const allPoints = (data ? extractPoints(data) : []).slice().sort((a, b) =>
    (b.date ?? '').localeCompare(a.date ?? '') || (b.version ?? '').localeCompare(a.version ?? ''),
  )
  const pagePoints = allPoints.slice(
    (snapshotPage - 1) * SNAPSHOT_PAGE_SIZE,
    snapshotPage * SNAPSHOT_PAGE_SIZE,
  )

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <span className="flex items-center gap-2">
            <Icon className="size-4" />
            <span>{title}</span>
            {subtitle && <span className="text-[10px] font-normal text-ink-faint">{subtitle}</span>}
          </span>
          <div className="flex items-center gap-2">
            {defaults && defaults.length > 0 && (
              <SearchableSelect
                value={data ? idOf(data) : ''}
                placeholder={selectPlaceholder}
                options={(defaults ?? []).map((d) => ({ value: idOf(d), label: nameOf(d) }))}
                loading={searchLoading}
                pageSize={10}
                onSearch={(q) => search(q)}
                onSelect={(v) => {
                  const hit = defaults?.find((d) => idOf(d) === v)
                  if (hit) {
                    setData(hit)
                    setSnapshotPage(1)
                    setError(null)
                  }
                }}
              />
            )}
            <Input
              value={idInput}
              onChange={(e) => setIdInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') load()
              }}
              placeholder={idPlaceholder}
              className="h-8 w-56 font-mono text-xs"
            />
            <Button size="sm" variant="outline" className="h-8" onClick={load} disabled={loading}>
              {loading ? '查询中…' : '查询'}
            </Button>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {defaultError && <p className="py-6 text-center text-xs text-state-archived">{defaultError}</p>}
        {!defaultError && defaults === null && !error && (
          <p className="py-6 text-center text-xs text-ink-faint">{loadingMsg}</p>
        )}
        {!defaultError && defaults !== null && defaults.length === 0 && !error && (
          <p className="py-6 text-center text-xs text-ink-faint">{noDataMsg}</p>
        )}
        {error && <p className="py-6 text-center text-xs text-state-archived">{error}</p>}
        {!error && data && allPoints.length === 0 && (
          <p className="py-6 text-center text-xs text-ink-faint">{emptyMsg}</p>
        )}
        {!error && data && allPoints.length > 0 && (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-ink-muted">
              <span className="font-medium text-ink">{nameOf(data)}</span>
              <span className="font-mono text-[10px] text-ink-faint">{idOf(data)}</span>
              <span className="text-ink-faint">· 共 {allPoints.length} 期快照</span>
            </div>
            <PointsTrendChart points={allPoints} freqLabel={freqLabel} />
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>快照日期</TableHead>
                  <TableHead>版本</TableHead>
                  <TableHead className="text-right">{freqLabel}</TableHead>
                  {extraColumns && <TableHead className="text-right">快照中存在</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {pagePoints.map((p) => (
                  <TableRow key={p.version}>
                    <TableCell className="text-xs font-mono text-ink-muted">{p.date ?? '—'}</TableCell>
                    <TableCell className="font-mono text-xs text-ink-secondary">{p.version}</TableCell>
                    <TableCell className="text-right tabular-nums font-mono">{p.freq}</TableCell>
                    {extraColumns?.(data, p)}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {/* 快照时间线翻页（10 期/页、最新在前，08-16 用户决策） */}
            {allPoints.length > SNAPSHOT_PAGE_SIZE && (
              <PaginationBar
                page={snapshotPage}
                total={allPoints.length}
                pageSize={SNAPSHOT_PAGE_SIZE}
                onPageChange={setSnapshotPage}
              />
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

// ===== PointsTrendChart / SkillFlowView（时序可视化增强） =====

/** 快照频次折线 + 时间轴滑窗播放（SkillsFlow 前置于表格，答辩演示动态感）。

 * ECharts line + dataZoom slider；播放=定时步进 3 期窗口（dispatchAction），
 * 表格仍保留在下方作数据对照。图表实例独立 useEffect 生命周期 + ResizeObserver
 * 安全 resize（与 graph-community-tree 同范式）。
 */
function PointsTrendChart({ points, freqLabel }: { points: SnapshotPoint[]; freqLabel: string }) {
  const elRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const [playing, setPlaying] = useState(false)
  const cursorRef = useRef(0)
  const timerRef = useRef<number | null>(null)
  // 时间升序（表格展示最新在前，图表从左到右按时间演进）
  const asc = useMemo(
    () => [...points].sort((a, b) =>
      (a.date ?? a.version ?? '').localeCompare(b.date ?? b.version ?? '')),
    [points],
  )
  const labels = asc.map((p) => p.date ?? p.version ?? '—')

  useEffect(() => {
    const el = elRef.current
    if (!el || asc.length === 0) return
    const dark = isDark()
    const chart = echarts.init(el)
    chartRef.current = chart
    const muted = dark ? '#94a3b8' : '#64748b'
    const axisColor = dark ? '#334155' : '#e2e8f0'
    chart.setOption({
      animation: true,
      grid: { left: 48, right: 16, top: 24, bottom: 56 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: dark ? '#1e293b' : '#fff',
        borderColor: axisColor,
        textStyle: { color: dark ? '#e2e8f0' : '#1e293b', fontSize: 11 },
      },
      xAxis: {
        type: 'category',
        data: labels,
        axisLabel: { fontSize: 10, color: muted, rotate: labels.length > 12 ? 38 : 0 },
        axisLine: { lineStyle: { color: axisColor } },
      },
      yAxis: {
        type: 'value',
        name: freqLabel,
        nameTextStyle: { color: muted, fontSize: 10 },
        axisLabel: { fontSize: 10, color: muted },
        splitLine: { lineStyle: { color: axisColor, opacity: 0.4 } },
      },
      dataZoom: [
        { type: 'inside' },
        {
          type: 'slider',
          height: 14,
          bottom: 10,
          borderColor: axisColor,
          textStyle: { color: muted, fontSize: 9 },
        },
      ],
      series: [
        {
          type: 'line',
          data: asc.map((p) => p.freq ?? 0),
          smooth: true,
          symbolSize: 6,
          lineStyle: { width: 2 },
          areaStyle: { opacity: 0.12 },
          emphasis: { focus: 'series' },
        },
      ],
    })
    const observer = new ResizeObserver(() => chartRef.current?.resize())
    observer.observe(el)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [asc, freqLabel, labels])

  // 播放/暂停：800ms 步进 3 期滑窗，到尾回绕
  function togglePlay() {
    if (playing) {
      if (timerRef.current) window.clearInterval(timerRef.current)
      timerRef.current = null
      setPlaying(false)
      return
    }
    setPlaying(true)
    timerRef.current = window.setInterval(() => {
      const chart = chartRef.current
      if (!chart || asc.length === 0) return
      const windowSize = Math.min(3, asc.length)
      cursorRef.current = (cursorRef.current + 1) % asc.length
      const end = Math.min(cursorRef.current + windowSize, asc.length - 1)
      chart.dispatchAction({
        type: 'dataZoom',
        dataZoomIndex: 0,
        startValue: Math.max(0, end - windowSize + 1),
        endValue: end,
      })
    }, 800)
  }

  useEffect(
    () => () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
    },
    [],
  )

  if (asc.length === 0) return null
  return (
    <div className="mb-4">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[10px] text-ink-faint">
          时间轴播放：拖动滑窗或点击播放回放{freqLabel}演进
        </span>
        <Button size="sm" variant="outline" className="h-6 px-2 text-[10px]" onClick={togglePlay}>
          {playing ? <Pause className="mr-1 size-3" /> : <Play className="mr-1 size-3" />}
          {playing ? '暂停' : '播放'}
        </Button>
      </div>
      <div ref={elRef} className="h-56 w-full" />
    </div>
  )
}

/** 后端 /evolution/skill/{id}/flow 返回项（桑基图三元组） */
type SkillFlowData = components['schemas']['SkillFlowData']

/** 技能关联岗位动态变迁桑基图：列=快照期次，节点=该期 Top-N 岗位，
 * 连线=相邻期同名岗位（值=左侧期次频次）——输入技能看关联岗位进出变迁。 */
function SkillFlowView() {
  const [skills, setSkills] = useState<SkillEvolutionData[] | null>(null)
  const [skillId, setSkillId] = useState('')
  const [flow, setFlow] = useState<SkillFlowData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const elRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  function fetchFlow(id: string) {
    if (!id) return
    setLoading(true)
    apiGet<SkillFlowData>(`/evolution/skill/${encodeURIComponent(id)}/flow?top=8`)
      .then((r) => {
        setFlow(r)
        setError(null)
      })
      .catch((e) => setError(errMsg(e, '岗位变迁加载失败')))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    let cancelled = false
    apiGet<SkillEvolutionListData>('/evolution/skills?page=1&size=50')
      .then((r) => {
        if (cancelled) return
        setSkills(r.skills)
        if (r.skills.length > 0) {
          setSkillId(r.skills[0].skill_id)
          fetchFlow(r.skills[0].skill_id)
        }
      })
      .catch((e) => {
        if (!cancelled) setError(errMsg(e, '技能列表加载失败'))
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    const el = elRef.current
    if (!el || !flow || flow.nodes.length === 0) return
    const dark = isDark()
    const axisColor = dark ? '#334155' : '#e2e8f0'
    const chart = echarts.init(el)
    chartRef.current = chart
    const nameOf = new Map(flow.nodes.map((n) => [n.id, n.name]))
    chart.setOption({
      animation: true,
      tooltip: {
        trigger: 'item',
        backgroundColor: dark ? '#1e293b' : '#fff',
        borderColor: axisColor,
        textStyle: { color: dark ? '#e2e8f0' : '#1e293b', fontSize: 11 },
        formatter: (p: {
          dataType: string
          data: { source?: string; target?: string; name?: string; value?: number }
        }) => {
          if (p.dataType === 'edge') {
            const from = nameOf.get(p.data.source ?? '')
            const to = nameOf.get(p.data.target ?? '')
            return from === to
              ? `${from}<br/>持续需求 · 频次 ${p.data.value}`
              : `${from} → ${to}<br/>频次 ${p.data.value}`
          }
          const node = flow.nodes.find((n) => n.id === p.data.name)
          if (!node) return ''
          return `${node.name}<br/>${flow.periods[node.period_index] ?? '—'} · 频次 ${node.freq}`
        },
      },
      series: [
        {
          type: 'sankey',
          left: 16,
          right: 130,
          top: 16,
          bottom: 16,
          nodeWidth: 12,
          nodeGap: 6,
          emphasis: { focus: 'adjacency' },
          label: {
            fontSize: 10,
            color: dark ? '#cbd5e1' : '#334155',
            formatter: (p: { data: { name?: string } }) =>
              nameOf.get(p.data.name ?? '') ?? p.data.name ?? '',
          },
          lineStyle: { color: 'gradient', curveness: 0.5, opacity: 0.35 },
          data: flow.nodes.map((n) => ({ name: n.id })),
          links: flow.links.map((l) => ({
            source: l.source,
            target: l.target,
            value: l.value,
          })),
        },
      ],
    })
    const observer = new ResizeObserver(() => chartRef.current?.resize())
    observer.observe(el)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [flow])

  return (
    <Card className="mb-4">
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <span className="flex items-center gap-2">
            <GitBranch className="size-4" />
            <span>技能关联岗位变迁桑基图</span>
            <span className="text-[10px] font-normal text-ink-faint">
              输入技能 → 各期 Top-8 关联岗位进出与持续需求厚度
            </span>
          </span>
          <div className="flex items-center gap-2">
            {skills && skills.length > 0 && (
              <SearchableSelect
                value={skillId}
                placeholder="选择技能"
                options={skills.map((s) => ({ value: s.skill_id, label: s.skill_name }))}
                pageSize={10}
                onSelect={(v) => {
                  setSkillId(v)
                  fetchFlow(v)
                }}
              />
            )}
            {loading && <span className="text-[10px] text-ink-faint">加载中…</span>}
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {error && <p className="py-6 text-center text-xs text-state-archived">{error}</p>}
        {!error && flow === null && !loading && (
          <p className="py-6 text-center text-xs text-ink-faint">暂无岗位变迁数据（版本数据不足）</p>
        )}
        {!error && flow !== null && flow.nodes.length === 0 && (
          <p className="py-6 text-center text-xs text-ink-faint">该技能在各版本快照中无关联岗位</p>
        )}
        {flow && flow.nodes.length > 0 && (
          <>
            <div ref={elRef} className="h-96 w-full" />
            <p className="mt-1 text-[10px] text-ink-faint">
              {flow.skill_name} · 共 {flow.periods.length} 期快照（
              {flow.periods[0] ?? '—'} → {flow.periods[flow.periods.length - 1] ?? '—'}）·
              连线粗细=左侧期次 REQUIRES 频次
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}

// ===== 技能频次趋势 / 岗位演化历史（SnapshotTimelineView 配置） =====

function SkillTrendView() {
  return (
    <SnapshotTimelineView<SkillEvolutionData>
      icon={TrendingUp}
      title="技能频次趋势 · 最近 90 天"
      selectPlaceholder="选择技能"
      idPlaceholder="技能节点 ID（sk_xxxx）"
      idErrorMsg="请输入技能节点 ID（如 sk_xxxx）"
      defaultErrorMsg="默认技能加载失败"
      loadErrorMsg="趋势查询失败"
      loadingMsg="加载默认技能趋势…"
      noDataMsg="暂无技能快照数据（版本数据不足），可输入技能节点 ID 查询"
      emptyMsg="该技能在各版本快照中无关联边"
      listUrl="/evolution/skills?page=1&size=50"
      searchUrl={(q) => `/evolution/skills?page=1&size=50&q=${encodeURIComponent(q)}`}
      detailUrl={(id) => `/evolution/trends?skill=${encodeURIComponent(id)}&window=90`}
      idOf={(d) => d.skill_id}
      nameOf={(d) => (d as { skill_name?: string; skill?: string }).skill_name ?? (d as { skill?: string }).skill ?? d.skill_id}
      extractList={(r) => (r as SkillEvolutionListData).skills}
      extractPoints={(d) => d.points ?? []}
      freqLabel="关联岗位数"
    />
  )
}

function PositionEvolutionView() {
  return (
    <SnapshotTimelineView<PositionEvolutionData>
      icon={Boxes}
      title="岗位演化历史"
      subtitle="各版本快照中的存在性与关联技能边数"
      selectPlaceholder="选择岗位"
      idPlaceholder="岗位节点 ID（pos_xxxx）"
      idErrorMsg="请输入岗位节点 ID（如 pos_xxxx）"
      defaultErrorMsg="默认岗位加载失败"
      loadErrorMsg="演化历史查询失败"
      loadingMsg="加载默认岗位演化…"
      noDataMsg="暂无岗位快照数据（版本数据不足），可输入岗位节点 ID 查询"
      emptyMsg="该岗位在各版本快照中均未出现"
      listUrl="/evolution/positions?page=1&size=50"
      searchUrl={(q) => `/evolution/positions?page=1&size=50&q=${encodeURIComponent(q)}`}
      detailUrl={(id) => `/evolution/position/${encodeURIComponent(id)}/evolution`}
      idOf={(d) => d.position_id}
      nameOf={(d) => d.position_name}
      extractList={(r) => (r as PositionEvolutionListData).positions}
      extractPoints={(d) => d.points ?? []}
      freqLabel="关联技能边数"
      extraColumns={(_d, p) => (
        <TableCell className="text-right">
          {p.present ? (
            <Badge variant="outline" className="text-[10px] text-state-stable">存在</Badge>
          ) : (
            <Badge variant="outline" className="text-[10px] text-ink-faint">未收录</Badge>
          )}
        </TableCell>
      )}
    />
  )
}

// ===== MetricCard =====

function MetricCard({ metric }: { metric: MetricItem }) {
  const toneColor =
    metric.tone === 'emerging'
      ? 'text-state-emerging'
      : metric.tone === 'declining'
        ? 'text-state-declining'
        : 'text-state-stable'
  const toneBg =
    metric.tone === 'emerging'
      ? 'bg-state-emerging/10'
      : metric.tone === 'declining'
        ? 'bg-state-declining/10'
        : 'bg-state-stable/10'

  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-ink-muted">{metric.label}</span>
          <span className={cn('inline-flex items-center gap-0.5 text-xs font-mono', toneColor)}>
            {metric.delta > 0 ? '+' : ''}{metric.delta}
          </span>
        </div>
        <div className="text-2xl font-semibold tracking-tight tabular-nums">
          {typeof metric.value === 'number' ? (
            metric.value.toLocaleString()
          ) : (
            <span className="font-mono">{metric.value}</span>
          )}
        </div>
        <div className="text-[10px] text-ink-faint mt-1 truncate">{metric.hint}</div>
        <div className={cn('mt-2 h-0.5 rounded-full', toneBg)} />
      </CardContent>
    </Card>
  )
}

// ===== VersionDiffView =====

/** 从节点 ID 前缀推断类型（id_generator 约定 pos_/sk_/ev_/co_） */
function typeOf(id: string): VersionDiffItem['type'] {
  if (id.startsWith('pos_')) return 'position'
  if (id.startsWith('ev_')) return 'evidence'
  if (id.startsWith('co_')) return 'course'
  return 'skill'
}

/** 节点类型：优先用后端快照 type（course/tool 等精确类型），未知时回退 id 前缀推断 */
function nodeTypeOf(n: EvolutionDiffNode): VersionDiffItem['type'] {
  if (n.type === 'position' || n.type === 'skill' || n.type === 'evidence' || n.type === 'course' || n.type === 'tool') {
    return n.type
  }
  return typeOf(n.id)
}

function diffToItems(d: EvolutionDiff): {
  added: VersionDiffItem[]
  removed: VersionDiffItem[]
  changed: VersionDiffItem[]
} {
  const toItems = (
    list: EvolutionDiffNode[],
    change: VersionDiffItem['change'],
    detail: string,
  ): VersionDiffItem[] =>
    list.map((n) => ({ id: n.id, name: n.name, type: nodeTypeOf(n), change, detail }))
  return {
    added: toItems(d.nodes_added, 'added', '节点新增'),
    removed: toItems(d.nodes_removed, 'removed', '节点删除'),
    changed: toItems(d.nodes_changed, 'changed', '两版本共有（交集节点）'),
  }
}

function VersionDiffView() {
  const [versions, setVersions] = useState<EvolutionVersion[]>([])
  const [v1, setV1] = useState<string>('')
  const [v2, setV2] = useState<string>('')
  // 08-16 用户决策：版本下拉全量可搜索（保留 90 天 ≈ ≤90 个版本，一次拉全，
  // 移除列表分页——原分页条在巨型 diff 表下方不可见且翻页后下拉选项不全）
  const [diff, setDiff] = useState<{
    v1: string
    v2: string
    data: { added: VersionDiffItem[]; removed: VersionDiffItem[]; changed: VersionDiffItem[] }
  } | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 版本详情弹窗（GET /evolution/versions/{id}）
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailVersion, setDetailVersion] = useState('')
  const [detail, setDetail] = useState<EvolutionVersionDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  function loadDetail(id: string) {
    setDetailOpen(true)
    setDetailVersion(id)
    setDetail(null)
    setDetailError(null)
    setDetailLoading(true)
    apiGet<EvolutionVersionDetail>(`/evolution/versions/${encodeURIComponent(id)}`)
      .then(setDetail)
      .catch((e) => setDetailError(errMsg(e, '版本详情加载失败')))
      .finally(() => setDetailLoading(false))
  }

  // 加载全量版本列表（size=100，覆盖 90 天保留期），默认对比最近两个版本。
  // 初始加载不设 loading 态（effect 内同步 setState 违反 react-hooks/set-state-in-effect）
  useEffect(() => {
    let cancelled = false
    apiGet<components['schemas']['EvolutionVersionListData']>(`/evolution/versions?page=1&size=100`)
      .then((res) => {
        if (cancelled) return
        // 快照可能在同一事务写入导致 created_at 相同，按 version_id（graph_vYYYYMMDD）降序保证稳定
        const items = [...res.items].sort((a, b) => b.version_id.localeCompare(a.version_id))
        setVersions(items)
        // 默认对比最近两个版本（首次加载/尚未选择时）
        if (!v1 && !v2) {
          if (items.length >= 2) {
            setV1(items[1].version_id)
            setV2(items[0].version_id)
          } else if (items.length === 1) {
            setV1(items[0].version_id)
          }
        }
      })
      .catch(() => setError('版本列表加载失败'))
    return () => {
      cancelled = true
    }
  }, [])

  // 版本对变化 → 拉取真实 diff（setState 均在异步回调内）
  useEffect(() => {
    if (!v1 || !v2 || v1 === v2) return
    let cancelled = false
    apiGet<EvolutionDiff>(
      `/evolution/diff?from=${encodeURIComponent(v1)}&to=${encodeURIComponent(v2)}`,
    )
      .then((d) => {
        if (!cancelled) setDiff({ v1, v2, data: diffToItems(d) })
      })
      .catch(() => {
        if (!cancelled) setError('版本对比加载失败')
      })
    return () => {
      cancelled = true
    }
  }, [v1, v2])

  // 渲染派生：仅展示与当前版本对匹配的 diff；加载态 = 有版本对但结果未就绪
  const visibleDiff = diff && diff.v1 === v1 && diff.v2 === v2 ? diff.data : null
  const loading = Boolean(v1 && v2 && v1 !== v2 && !visibleDiff && !error)

  return (
    <>
      <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <span className="flex items-center gap-2">
            <GitBranch className="size-4" />
            <span>版本快照对比</span>
          </span>
          <div className="flex items-center gap-1.5">
            <SearchableSelect
              value={v1}
              placeholder="选择版本"
              options={versions.map((v) => ({ value: v.version_id, label: v.version_id }))}
              pageSize={10}
              onSelect={setV1}
            />
            <span className="text-xs text-ink-faint">vs</span>
            <SearchableSelect
              value={v2}
              placeholder="选择版本"
              options={versions.map((v) => ({ value: v.version_id, label: v.version_id }))}
              pageSize={10}
              onSelect={setV2}
            />
            <Button
              size="sm"
              variant="ghost"
              className="h-8 px-2 text-xs"
              disabled={versions.length === 0}
              onClick={() => loadDetail(v2 || versions[0]?.version_id)}
            >
              <Eye className="size-3.5 mr-1" />
              版本详情
            </Button>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {error && <div className="py-10 text-center text-xs text-state-archived">{error}</div>}
        {!error && versions.length === 0 && (
          <div className="py-10 text-center text-xs text-ink-muted">暂无图谱版本快照</div>
        )}
        {!error && versions.length > 0 && !v1 && !v2 && (
          <div className="py-10 text-center text-xs text-ink-muted">仅存在单个版本，无法对比</div>
        )}
        {loading && (
          <div className="py-10 text-center text-xs text-ink-muted">加载版本差异…</div>
        )}
        {!loading && visibleDiff && (
          <>
            <div className="grid grid-cols-3 gap-3 mb-4">
              <StatTile label="新增节点" count={visibleDiff.added.length} tone="emerging" />
              <StatTile label="删除节点" count={visibleDiff.removed.length} tone="declining" />
              <StatTile label="共有节点" count={visibleDiff.changed.length} tone="stable" />
            </div>
            <Tabs defaultValue="added">
              <TabsList>
                <TabsTrigger value="added" className="text-xs">新增 ({visibleDiff.added.length})</TabsTrigger>
                <TabsTrigger value="removed" className="text-xs">删除 ({visibleDiff.removed.length})</TabsTrigger>
                <TabsTrigger value="changed" className="text-xs">共有 ({visibleDiff.changed.length})</TabsTrigger>
              </TabsList>
              <TabsContent value="added">
                <PaginatedDiffTable key={`${v1}:${v2}`} items={visibleDiff.added} />
              </TabsContent>
              <TabsContent value="removed">
                <PaginatedDiffTable key={`${v1}:${v2}`} items={visibleDiff.removed} />
              </TabsContent>
              <TabsContent value="changed">
                <PaginatedDiffTable key={`${v1}:${v2}`} items={visibleDiff.changed} />
              </TabsContent>
            </Tabs>
          </>
        )}
        {!loading && !error && versions.length > 0 && v1 && v2 && v1 === v2 && (
          <div className="py-10 text-center text-xs text-ink-muted">请选择两个不同版本进行对比</div>
        )}
      </CardContent>
    </Card>

    {/* 版本详情弹窗（真实 GET /evolution/versions/{id}） */}
    <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>版本详情：{detailVersion}</DialogTitle>
          <DialogDescription>
            {detail?.created_at ?? '加载中…'} · 快照节点 {detail?.stats.nodes ?? '—'} · 边 {detail?.stats.edges ?? '—'}
          </DialogDescription>
        </DialogHeader>
        {detailLoading && (
          <div className="py-10 text-center text-xs text-ink-muted">加载版本详情…</div>
        )}
        {detailError && (
          <div className="py-10 text-center text-xs text-state-archived">{detailError}</div>
        )}
        {detail && (
          <div className="space-y-4">
            {/* 节点类型分布 */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {Object.entries(detail.stats.by_type ?? {}).map(([type, count]) => (
                <div key={type} className="rounded-md bg-subtle p-2 text-center">
                  <div className="text-lg font-semibold tabular-nums text-ink">{count}</div>
                  <div className="text-[10px] text-ink-muted">{type}</div>
                </div>
              ))}
            </div>
            {/* 变更摘要 */}
            {detail.change_summary && (
              <p className="rounded-md border border-border bg-subtle/40 p-2.5 text-xs text-ink-secondary leading-relaxed">
                {detail.change_summary}
              </p>
            )}
            {/* 节点列表（前 50 条，避免超载） */}
            <div className="max-h-72 overflow-y-auto rounded-md border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[160px]">节点 ID</TableHead>
                    <TableHead>名称</TableHead>
                    <TableHead className="w-[70px]">类型</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {detail.nodes.slice(0, 50).map((n) => (
                    <TableRow key={n.id}>
                      <TableCell className="font-mono text-[10px] text-ink-muted">{n.id}</TableCell>
                      <TableCell className="text-xs font-medium text-ink">{n.name}</TableCell>
                      <TableCell className="text-[10px] text-ink-faint">{n.type}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {detail.nodes.length > 50 && (
                <p className="border-t border-border p-2 text-center text-[10px] text-ink-faint">
                  仅显示前 50 条，共 {detail.nodes.length} 个节点
                </p>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
    </>
  )
}

function StatTile({ label, count, tone }: { label: string; count: number; tone: TrendTone }) {
  const color =
    tone === 'emerging'
      ? 'text-state-emerging'
      : tone === 'declining'
        ? 'text-state-declining'
        : 'text-state-stable'
  return (
    <div className="rounded-md border border-border p-3 bg-subtle/40">
      <div className={cn('text-2xl font-semibold tabular-nums', color)}>{count}</div>
      <div className="text-xs text-ink-muted mt-0.5">{label}</div>
    </div>
  )
}

function DiffTable({ items }: { items: VersionDiffItem[] }) {
  if (items.length === 0) {
    return <div className="py-6 text-center text-xs text-ink-faint">无数据</div>
  }
  const typeLabel: Record<VersionDiffItem['type'], string> = {
    position: '岗位',
    skill: '技能',
    evidence: '证据',
    course: '课程',
    tool: '工具',
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[150px]">节点名</TableHead>
          <TableHead>名称</TableHead>
          <TableHead className="w-[60px]">类型</TableHead>
          <TableHead>变化说明</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => (
          <TableRow key={item.id}>
            <TableCell className="font-mono text-xs text-ink-muted">{item.id}</TableCell>
            <TableCell className="font-medium text-ink">{item.name}</TableCell>
            <TableCell>
              <Badge variant="outline" className="text-[10px] font-mono">
                {typeLabel[item.type]}
              </Badge>
            </TableCell>
            <TableCell className="text-xs text-ink-muted">{item.detail}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** 版本 diff 表格分页（10 项一页，08-16 用户决策：新增/删除/共有三标签翻页）。
 *
 * 切换版本对时通过 key 重挂载重置页码；切换标签页时 Radix Tabs 卸载
 * 非激活内容，页码同样归位。
 */
const DIFF_PAGE_SIZE = 10

function PaginatedDiffTable({ items }: { items: VersionDiffItem[] }) {
  const [page, setPage] = useState(1)
  const slice = items.slice((page - 1) * DIFF_PAGE_SIZE, page * DIFF_PAGE_SIZE)
  return (
    <>
      <DiffTable items={slice} />
      {items.length > DIFF_PAGE_SIZE && (
        <PaginationBar
          page={page}
          total={items.length}
          pageSize={DIFF_PAGE_SIZE}
          onPageChange={setPage}
        />
      )}
    </>
  )
}

/** 六态元信息（与图谱状态机一致 + rejected 终态） */
const STATE_META: Record<
  string,
  { label: string; dot: string; badge: BadgeProps['variant'] }
> = {
  candidate: { label: '候选', dot: 'bg-state-candidate', badge: 'candidate' },
  emerging: { label: '新兴', dot: 'bg-state-emerging', badge: 'emerging' },
  stable: { label: '稳定', dot: 'bg-state-stable', badge: 'stable' },
  declining: { label: '衰退', dot: 'bg-state-declining', badge: 'declining' },
  archived: { label: '归档', dot: 'bg-state-archived', badge: 'archived' },
  rejected: { label: '驳回', dot: 'bg-state-archived', badge: 'archived' },
}

/** 六态分布 + 最近流转记录（GET /evolution/state-machine） */
type StateMachineData = components['schemas']['StateMachineData']

/** 岗位状态机流转（真实 GET /evolution/state-machine，六态分布 + 人工流转记录） */
function StateMachineView() {
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
          <span className="text-[10px] font-normal text-ink-faint">
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
              {Object.entries(STATE_META).map(([state, meta]) => (
                <div key={state} className="rounded-md border border-border p-2.5">
                  <div className="flex items-center gap-1.5 text-xs font-medium text-ink">
                    <span className={`size-2 rounded-full ${meta.dot}`} />
                    {meta.label}
                  </div>
                  <div className="mt-1 text-xl font-semibold tabular-nums">{data.states[state] ?? 0}</div>
                </div>
              ))}
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
                            <Badge variant={STATE_META[t.from_state ?? '']?.badge ?? 'outline'} className="text-[9px]">
                              {t.from_state}
                            </Badge>
                            <span className="text-ink-faint">→</span>
                            <Badge variant={STATE_META[t.to_state ?? '']?.badge ?? 'outline'} className="text-[9px]">
                              {t.to_state}
                            </Badge>
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
function EvolutionEventsView() {
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
          <span className="text-[10px] font-normal text-ink-faint">
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
                        className="text-[10px] inline-flex items-center gap-1"
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
                    <TableCell className="font-mono text-[10px] text-ink-faint">{ev.version_id}</TableCell>
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
function DataWarningBanner({ warning }: { warning: NonNullable<EvolutionVersion['data_warning']> }) {
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
            <Badge variant={e.direction === 'shrunk' ? 'declining' : 'emerging'} className="text-[10px]">
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

// ===== Page =====

export function EvolutionPage() {
  const [versions, setVersions] = useState<EvolutionVersion[]>([])

  // 加载真实版本列表（顶部指标 + diff 下拉共用），按 version_id 降序保证稳定
  useEffect(() => {
    apiGet<components['schemas']['EvolutionVersionListData']>('/evolution/versions?page=1&size=30')
      .then((res) => setVersions([...res.items].sort((a, b) => b.version_id.localeCompare(a.version_id))))
      .catch(() => {
        /* diff 视图内会提示错误 */
      })
  }, [])

  const metrics = useMemo<MetricItem[]>(() => {
    const latest = versions[0]
    return [
      { key: 'total', label: '图谱版本数', value: versions.length, delta: versions.length, tone: 'stable', hint: 'T+1 05:00 发布 · 保留 90 天' },
      { key: 'version', label: '当前版本号', value: latest?.version_id ?? '—', delta: 0, tone: 'stable', hint: latest?.change_summary || '暂无版本快照' },
      { key: 'nodes', label: '最新版本节点变化', value: latest ? latest.node_added + latest.node_changed : 0, delta: latest?.node_added ?? 0, tone: 'emerging', hint: `新增 ${latest?.node_added ?? 0} · 变化 ${latest?.node_changed ?? 0}` },
      { key: 'signals', label: '新兴/衰退信号', value: '—', delta: 0, tone: 'stable', hint: '下方"新兴/衰退技能 Top-10"实时展示' },
    ]
  }, [versions])

  return (
    <>
      <PageHeader
        title="演化看板"
        description="图谱版本快照追踪技能频次变化，Z-score 检测新兴/衰退信号 · 岗位状态机生命周期管理"
        actions={
          <Badge variant="outline" className="font-mono text-xs">
            <Calendar className="size-3 mr-1" />
            T+1 05:00 发布
          </Badge>
        }
      />

      {/* 样本量波动告警 + 顶部指标卡（真实版本派生） */}
      {versions[0]?.data_warning && <DataWarningBanner warning={versions[0].data_warning} />}

      {/* C 端技能衰退预警摘要（真实 /evolution/signals declining） */}
      <SkillDeclineWarningCard />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
        {metrics.map((m) => (
          <MetricCard key={m.key} metric={m} />
        ))}
      </div>

      {/* 岗位演化历史（真实 /evolution/position/{id}/evolution） */}
      <div className="mb-4">
        <PositionEvolutionView />
      </div>

      {/* 技能频次趋势（真实 /evolution/trends） */}
      <div className="mb-4">
        <SkillTrendView />
      </div>

      {/* 技能关联岗位动态变迁桑基图（真实 /evolution/skill/{id}/flow） */}
      <SkillFlowView />

      {/* 新兴 / 衰退技能 Top-10（真实 /evolution/signals） */}
      <SignalsView />

      {/* 技术热点观察池（真实 /evolution/watch，MLI 产业化拐点） */}
      <TechnologyWatchView />

      {/* 版本快照对比（真实） */}
      <div className="mb-4">
        <VersionDiffView />
      </div>

      {/* 岗位状态机流转（真实 /evolution/state-machine） */}
      <StateMachineView />

      {/* 谱系事件流（真实 /evolution/events，新增/合并/终结） */}
      <div className="mt-4">
        <EvolutionEventsView />
      </div>
    </>
  )
}
