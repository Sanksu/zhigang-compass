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
import { useEffect, useMemo, useState } from 'react'
import { Calendar, GitBranch, TrendingUp, TrendingDown, Eye, Boxes } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge, type BadgeProps } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { PaginationBar } from '@/components/ui/pagination'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import { apiGet, ApiError } from '@/lib/api'
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

/** 后端 /evolution/trends 返回项 */
type EvolutionTrends = components['schemas']['EvolutionTrendsData']

/** 后端 /evolution/signals 返回项（EvolutionSignal 序列化） */
type EvolutionSignal = components['schemas']['EvolutionSignal']

type EvolutionSignalsData = components['schemas']['EvolutionSignalsData']

/** 后端 /evolution/versions/{id} 返回的版本详情 */
type EvolutionVersionDetail = components['schemas']['EvolutionVersionDetail']

/** 后端 /evolution/position/{id}/evolution 返回项 */
type PositionEvolutionData = components['schemas']['PositionEvolutionData']

/** 后端 /evolution/positions 返回项（默认岗位演化列表） */
type PositionEvolutionListData = components['schemas']['PositionEvolutionListData']

// ===== SignalsView =====

/** 新兴/衰退技能 Top-N（真实 GET /evolution/signals） */
function SignalsView() {
  const [data, setData] = useState<EvolutionSignalsData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiGet<EvolutionSignalsData>('/evolution/signals?top_n=10')
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : '信号加载失败'))
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
            <TableHead className="text-right">置信度</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((s, i) => (
            <TableRow key={s.skill_id}>
              <TableCell className="text-xs font-mono text-ink-faint">{i + 1}</TableCell>
              <TableCell className="font-medium text-ink">{s.skill_name}</TableCell>
              <TableCell className={cn('text-right font-mono tabular-nums', toneColor)}>
                {s.z_score != null ? s.z_score.toFixed(2) : '—'}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums text-ink-secondary">{s.current_freq}</TableCell>
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
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
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

function TechnologyWatchView() {
  const [data, setData] = useState<WatchItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 08-16 翻页：观察池 10 项一页（GET /evolution/watch page/size）
  const PAGE_SIZE = 10
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [pageLoading, setPageLoading] = useState(false)

  function loadPage(p: number) {
    setPageLoading(true)
    apiGet<components['schemas']['WatchOverviewData']>(`/evolution/watch?page=${p}&size=${PAGE_SIZE}`)
      .then((r) => {
        setData(r.items)
        setTotal(r.total)
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : '技术热点加载失败'))
      .finally(() => setPageLoading(false))
  }

  useEffect(() => {
    apiGet<components['schemas']['WatchOverviewData']>(`/evolution/watch?page=1&size=${PAGE_SIZE}`)
      .then((r) => {
        setData(r.items)
        setTotal(r.total)
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : '技术热点加载失败'))
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

// ===== SkillTrendView =====

/** 后端 /evolution/skills 返回项（默认技能演化列表） */
type SkillEvolutionListData = components['schemas']['SkillEvolutionListData']

/** 技能频次趋势（默认展示 Top-8 技能，可下拉切换或输入 ID 查特定技能） */
function SkillTrendView() {
  const [skillId, setSkillId] = useState('')
  const [data, setData] = useState<EvolutionTrends | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 默认技能列表（08-15：页面打开即有趋势，无需先查节点 ID）
  const [defaults, setDefaults] = useState<components['schemas']['SkillEvolutionData'][] | null>(null)
  const [selected, setSelected] = useState<components['schemas']['SkillEvolutionData'] | null>(null)
  const [defaultError, setDefaultError] = useState<string | null>(null)
  // 08-16 用户决策：翻页针对快照时间线（10 期/页、最新在前），技能列表不翻页
  const SNAPSHOT_PAGE_SIZE = 10
  const [snapshotPage, setSnapshotPage] = useState(1)

  // 页面加载即拉取 Top-10 热度技能（GET /evolution/skills）
  useEffect(() => {
    let cancelled = false
    apiGet<SkillEvolutionListData>(`/evolution/skills?page=1&size=10`)
      .then((r) => {
        if (cancelled) return
        setDefaults(r.skills)
        if (r.skills.length > 0) setSelected(r.skills[0])
      })
      .catch((e) => {
        if (!cancelled) setDefaultError(e instanceof ApiError ? e.message : '默认技能加载失败')
      })
    return () => {
      cancelled = true
    }
  }, [])

  function load() {
    const id = skillId.trim()
    if (!id) {
      setError('请输入技能节点 ID（如 sk_xxxx）')
      return
    }
    setLoading(true)
    setError(null)
    apiGet<EvolutionTrends>(`/evolution/trends?skill=${encodeURIComponent(id)}&window=90`)
      .then((r) => {
        setData(r)
        setSnapshotPage(1)
      })
      .catch((e) => {
        setData(null)
        setError(e instanceof ApiError ? e.message : '趋势查询失败')
      })
      .finally(() => setLoading(false))
  }

  // 当前展示：手动查询结果优先，否则默认选中技能
  const points = data?.points ?? selected?.points ?? null
  const title = data ? data.skill : (selected?.skill_name ?? null)
  // 快照时间线：日期最新在前，按 10 期/页切片（08-16 用户决策）
  const allSnapshotPoints = (points ?? []).slice().sort((a, b) =>
    (b.date ?? '').localeCompare(a.date ?? '') || (b.version ?? '').localeCompare(a.version ?? ''),
  )
  const pagePoints = allSnapshotPoints.slice(
    (snapshotPage - 1) * SNAPSHOT_PAGE_SIZE,
    snapshotPage * SNAPSHOT_PAGE_SIZE,
  )

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <span className="flex items-center gap-2">
            <TrendingUp className="size-4" />
            <span>技能频次趋势 · 最近 90 天</span>
          </span>
          <div className="flex items-center gap-2">
            {defaults && defaults.length > 0 && (
              <Select
                value={selected?.skill_id ?? ''}
                onValueChange={(v) => {
                  const hit = defaults.find((d) => d.skill_id === v)
                  if (hit) {
                    setSelected(hit)
                    setSnapshotPage(1)
                    setData(null)
                    setError(null)
                  }
                }}
              >
                <SelectTrigger className="h-8 w-48 text-xs">
                  <SelectValue placeholder="选择技能" />
                </SelectTrigger>
                <SelectContent>
                  {defaults.map((d) => (
                    <SelectItem key={d.skill_id} value={d.skill_id} className="text-xs">
                      {d.skill_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <Input
              value={skillId}
              onChange={(e) => setSkillId(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') load()
              }}
              placeholder="技能节点 ID（sk_xxxx）"
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
          <p className="py-6 text-center text-xs text-ink-faint">加载默认技能趋势…</p>
        )}
        {!defaultError && defaults !== null && defaults.length === 0 && !error && (
          <p className="py-6 text-center text-xs text-ink-faint">
            暂无技能快照数据（版本数据不足），可输入技能节点 ID 查询
          </p>
        )}
        {error && <p className="py-6 text-center text-xs text-state-archived">{error}</p>}
        {!error && points && points.length === 0 && (
          <p className="py-6 text-center text-xs text-ink-faint">该技能在各版本快照中无关联边</p>
        )}
        {!error && points && points.length > 0 && (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-ink-muted">
              <span className="font-medium text-ink">{title}</span>
              <span className="font-mono text-[10px] text-ink-faint">
                {data?.skill ?? selected?.skill_id}
              </span>
              <span className="text-ink-faint">· 共 {points.length} 期快照</span>
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>快照日期</TableHead>
                  <TableHead>版本</TableHead>
                  <TableHead className="text-right">关联岗位数</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pagePoints.map((p) => (
                  <TableRow key={p.version}>
                    <TableCell className="text-xs font-mono text-ink-muted">{p.date ?? '—'}</TableCell>
                    <TableCell className="font-mono text-xs text-ink-secondary">{p.version}</TableCell>
                    <TableCell className="text-right tabular-nums font-mono">{p.freq}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {/* 快照时间线翻页（10 期/页、最新在前，08-16 用户决策） */}
            {allSnapshotPoints.length > SNAPSHOT_PAGE_SIZE && (
              <PaginationBar
                page={snapshotPage}
                total={allSnapshotPoints.length}
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

// ===== PositionEvolutionView =====

/** 岗位演化历史（默认展示 Top-8 岗位，可下拉切换或输入 ID 查特定岗位） */
function PositionEvolutionView() {
  const [positionId, setPositionId] = useState('')
  const [data, setData] = useState<PositionEvolutionData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 默认岗位列表（08-15：页面打开即有演化轨迹，无需先查节点 ID）
  const [defaults, setDefaults] = useState<PositionEvolutionData[] | null>(null)
  const [defaultError, setDefaultError] = useState<string | null>(null)
  // 08-16 用户决策：翻页针对快照时间线（10 期/页、最新在前），岗位列表不翻页
  const SNAPSHOT_PAGE_SIZE = 10
  const [snapshotPage, setSnapshotPage] = useState(1)

  // 页面加载即拉取 Top-10 热度岗位（GET /evolution/positions）
  useEffect(() => {
    let cancelled = false
    apiGet<PositionEvolutionListData>(`/evolution/positions?page=1&size=10`)
      .then((r) => {
        if (cancelled) return
        setDefaults(r.positions)
        if (r.positions.length > 0) setData(r.positions[0])
      })
      .catch((e) => {
        if (!cancelled) setDefaultError(e instanceof ApiError ? e.message : '默认岗位加载失败')
      })
    return () => {
      cancelled = true
    }
  }, [])

  function load() {
    const id = positionId.trim()
    if (!id) {
      setError('请输入岗位节点 ID（如 pos_xxxx）')
      return
    }
    setLoading(true)
    setError(null)
    apiGet<PositionEvolutionData>(`/evolution/position/${encodeURIComponent(id)}/evolution`)
      .then((r) => {
        setData(r)
        setSnapshotPage(1)
      })
      .catch((e) => {
        setData(null)
        setError(e instanceof ApiError ? e.message : '演化历史查询失败')
      })
      .finally(() => setLoading(false))
  }

  // 快照时间线：日期最新在前，按 10 期/页切片（08-16 用户决策）
  const allPoints = (data?.points ?? []).slice().sort((a, b) =>
    (b.date ?? '').localeCompare(a.date ?? '') || (b.version ?? '').localeCompare(a.version ?? ''),
  )
  const points = allPoints.slice(
    (snapshotPage - 1) * SNAPSHOT_PAGE_SIZE,
    snapshotPage * SNAPSHOT_PAGE_SIZE,
  )

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <span className="flex items-center gap-2">
            <Boxes className="size-4" />
            <span>岗位演化历史</span>
            <span className="text-[10px] font-normal text-ink-faint">各版本快照中的存在性与关联技能边数</span>
          </span>
          <div className="flex items-center gap-2">
            {defaults && defaults.length > 0 && (
              <Select
                value={data?.position_id ?? ''}
                onValueChange={(v) => {
                  const hit = defaults.find((d) => d.position_id === v)
                  if (hit) {
                    setData(hit)
                    setSnapshotPage(1)
                    setError(null)
                  }
                }}
              >
                <SelectTrigger className="h-8 w-48 text-xs">
                  <SelectValue placeholder="选择岗位" />
                </SelectTrigger>
                <SelectContent>
                  {defaults.map((d) => (
                    <SelectItem key={d.position_id} value={d.position_id} className="text-xs">
                      {d.position_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <Input
              value={positionId}
              onChange={(e) => setPositionId(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') load()
              }}
              placeholder="岗位节点 ID（pos_xxxx）"
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
          <p className="py-6 text-center text-xs text-ink-faint">加载默认岗位演化…</p>
        )}
        {!defaultError && defaults !== null && defaults.length === 0 && !error && (
          <p className="py-6 text-center text-xs text-ink-faint">
            暂无岗位快照数据（版本数据不足），可输入岗位节点 ID 查询
          </p>
        )}
        {error && <p className="py-6 text-center text-xs text-state-archived">{error}</p>}
        {!error && data && (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-ink-muted">
              <span className="font-medium text-ink">{data.position_name}</span>
              <span className="font-mono text-[10px] text-ink-faint">{data.position_id}</span>
              <span className="text-ink-faint">· 共 {data.points.length} 期快照</span>
            </div>
            {data.points.length === 0 ? (
              <p className="py-6 text-center text-xs text-ink-faint">该岗位在各版本快照中均未出现</p>
            ) : (
              <>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>快照日期</TableHead>
                      <TableHead>版本</TableHead>
                      <TableHead className="text-right">关联技能边数</TableHead>
                      <TableHead className="text-right">快照中存在</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {points.map((p) => (
                      <TableRow key={p.version}>
                        <TableCell className="text-xs font-mono text-ink-muted">{p.date ?? '—'}</TableCell>
                        <TableCell className="font-mono text-xs text-ink-secondary">{p.version}</TableCell>
                        <TableCell className="text-right tabular-nums font-mono">{p.freq}</TableCell>
                        <TableCell className="text-right">
                          {p.present ? (
                            <Badge variant="outline" className="text-[10px] text-state-stable">存在</Badge>
                          ) : (
                            <Badge variant="outline" className="text-[10px] text-ink-faint">未收录</Badge>
                          )}
                        </TableCell>
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
          </>
        )}
      </CardContent>
    </Card>
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
  // 08-16 翻页：版本列表 10 项一页（GET /evolution/versions page/size）
  const PAGE_SIZE = 10
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [pageLoading, setPageLoading] = useState(false)
  // diff 绑定请求时的版本对，渲染层据此判断当前结果是否仍有效（避免 effect 内同步 setState）
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
      .catch((e) => setDetailError(e instanceof ApiError ? e.message : '版本详情加载失败'))
      .finally(() => setDetailLoading(false))
  }

  function loadVersionsPage(p: number) {
    setPageLoading(true)
    apiGet<components['schemas']['EvolutionVersionListData']>(`/evolution/versions?page=${p}&size=${PAGE_SIZE}`)
      .then((res) => {
        // 快照可能在同一事务写入导致 created_at 相同，按 version_id（graph_vYYYYMMDD）降序保证稳定
        const items = [...res.items].sort((a, b) => b.version_id.localeCompare(a.version_id))
        setVersions(items)
        setTotal(res.total)
        // 默认对比最近两个版本（首次加载/尚未选择时；翻页后不覆盖已选版本对）
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
      .finally(() => setPageLoading(false))
  }

  // 加载第 1 页版本列表，默认对比最近两个版本。
  // 初始加载不调用 loadVersionsPage（内含同步 setPageLoading——
  // effect 内同步 setState 违反 react-hooks/set-state-in-effect，CI lint error）
  useEffect(() => {
    let cancelled = false
    apiGet<components['schemas']['EvolutionVersionListData']>(`/evolution/versions?page=1&size=${PAGE_SIZE}`)
      .then((res) => {
        if (cancelled) return
        // 快照可能在同一事务写入导致 created_at 相同，按 version_id（graph_vYYYYMMDD）降序保证稳定
        const items = [...res.items].sort((a, b) => b.version_id.localeCompare(a.version_id))
        setVersions(items)
        setTotal(res.total)
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
            <Select value={v1} onValueChange={setV1} disabled={versions.length === 0}>
              <SelectTrigger className="h-8 w-[150px] font-mono text-xs">
                <SelectValue placeholder="选择版本" />
              </SelectTrigger>
              <SelectContent>
                {versions.map((v) => (
                  <SelectItem key={v.version_id} value={v.version_id} className="font-mono text-xs">
                    {v.version_id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-xs text-ink-faint">vs</span>
            <Select value={v2} onValueChange={setV2} disabled={versions.length === 0}>
              <SelectTrigger className="h-8 w-[150px] font-mono text-xs">
                <SelectValue placeholder="选择版本" />
              </SelectTrigger>
              <SelectContent>
                {versions.map((v) => (
                  <SelectItem key={v.version_id} value={v.version_id} className="font-mono text-xs">
                    {v.version_id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
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
                <DiffTable items={visibleDiff.added} />
              </TabsContent>
              <TabsContent value="removed">
                <DiffTable items={visibleDiff.removed} />
              </TabsContent>
              <TabsContent value="changed">
                <DiffTable items={visibleDiff.changed} />
              </TabsContent>
            </Tabs>
          </>
        )}
        {!loading && !error && versions.length > 0 && v1 && v2 && v1 === v2 && (
          <div className="py-10 text-center text-xs text-ink-muted">请选择两个不同版本进行对比</div>
        )}
        {/* 版本列表翻页（10 项一页，08-16）——版本选择器随页切换 */}
        {versions.length > 0 && (
          <PaginationBar
            page={page}
            total={total}
            pageSize={PAGE_SIZE}
            loading={pageLoading}
            onPageChange={(p) => {
              setPage(p)
              loadVersionsPage(p)
            }}
          />
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

      {/* 顶部指标卡（真实版本派生） */}
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
    </>
  )
}
