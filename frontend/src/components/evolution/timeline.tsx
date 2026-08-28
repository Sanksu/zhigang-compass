/** 演化视图组件（从 evolution-page.tsx 抽出，第六轮审查拆分：页面 ≤800 行惯例）。 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Play, Pause, TrendingUp } from 'lucide-react'
import * as echarts from 'echarts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { PaginationBar } from '@/components/ui/pagination'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { apiGet, errMsg } from '@/lib/api'
import { isDark } from '@/lib/utils'
import { SearchableSelect } from './shared'

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

export function SnapshotTimelineView<T extends { points?: SnapshotPoint[] }>({
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
            {subtitle && <span className="text-[11px] font-normal text-ink-faint">{subtitle}</span>}
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
              <span className="font-mono text-[11px] text-ink-faint">{idOf(data)}</span>
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
export function PointsTrendChart({ points, freqLabel }: { points: SnapshotPoint[]; freqLabel: string }) {
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
        <span className="text-xs text-ink-faint">
          时间轴播放：拖动滑窗或点击播放回放{freqLabel}演进
        </span>
        <Button size="sm" variant="outline" className="h-7 px-2.5 text-xs" onClick={togglePlay}>
          {playing ? <Pause className="mr-1 size-3" /> : <Play className="mr-1 size-3" />}
          {playing ? '暂停' : '播放'}
        </Button>
      </div>
      <div ref={elRef} className="h-56 w-full" />
    </div>
  )
}

/** 后端 /evolution/skill/{id}/flow 返回项（桑基图三元组） */
