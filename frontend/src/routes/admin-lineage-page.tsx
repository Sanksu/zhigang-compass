import { useEffect, useMemo, useState } from 'react'
import { Database, FileSearch, Network, ShieldCheck, TriangleAlert } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { PaginationBar } from '@/components/ui/pagination'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
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
import { useIsDesktop } from '@/hooks/use-media-query'
import type { components } from '@/types/api'

type LineagePositionsData = components['schemas']['LineagePositionsData']
type LineagePositionItem = components['schemas']['LineagePositionItem']
type LineageDetail = components['schemas']['LineageDetail']

const PAGE_SIZE = 20

/** 置信度配色（对齐审核页 CONFIDENCE_TONE：高绿/中蓝/低橙） */
function confidenceTone(v: number): string {
  if (v >= 0.7) return 'text-state-stable'
  if (v >= 0.6) return 'text-state-emerging'
  return 'text-state-declining'
}

/** 薪资异常/低置信等警示统一 badge 语义 */
function warnBadge(variant: 'outline' | 'candidate' | 'declining', label: string) {
  return (
    <Badge variant={variant} className="text-[11px]">
      {label}
    </Badge>
  )
}

/**
 * 数据血缘溯源页 — P13 管理端可视化
 *
 * 将此前仅留存于 ETL 管线日志 / jd_raw 快照的溯源结果（岗位 ← 证据 JD ← 采集源）
 * 暴露为管理端可直观查看的视图：
 *  - 血缘总览统计（分组数/覆盖 JD/多源印证/已验证/低置信）
 *  - 岗位列表：组级跨源校验（技能 ≥2 源印证 / 薪资异常 / 经验分歧 / 跨源置信度）
 *  - 详情弹窗：组内每条证据 JD（source / source_url / crawled_at / city / salary /
 *    skills / 是否 SimHash 去重标记），可溯源跳转到原始招聘页。
 */
export function AdminLineagePage() {
  const [items, setItems] = useState<LineagePositionItem[]>([])
  const [summary, setSummary] = useState<LineagePositionsData['summary'] | null>(null)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const isDesktop = useIsDesktop()

  /* 过滤条件：关键字 / 仅已验证 / 仅低置信 */
  const [q, setQ] = useState('')
  const [onlyVerified, setOnlyVerified] = useState(false)
  const [onlyLowConfidence, setOnlyLowConfidence] = useState(false)

  /* 详情弹窗 */
  const [detail, setDetail] = useState<LineageDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  // 查询参数统一序列化（避免手动拼 URLSearchParams 遗漏）
  const params = useMemo(() => {
    const p = new URLSearchParams({ page: String(page), size: String(PAGE_SIZE) })
    if (q.trim()) p.set('q', q.trim())
    if (onlyVerified) p.set('verified', 'true')
    if (onlyLowConfidence) p.set('below_confidence', 'true')
    return p.toString()
  }, [q, onlyVerified, onlyLowConfidence, page])

  useEffect(() => {
    // 请求仅在异步回调中 setState（react-hooks v7 禁 effect 内同步 setState）；
    // loading 初始 true，refetch 沿用旧数据直至新结果返回（无加载闪烁）
    let cancelled = false
    apiGet<LineagePositionsData>(`/admin/lineage/positions?${params}`)
      .then((res) => {
        if (cancelled) return
        setError(null)
        setItems(res.items)
        setTotal(res.total)
        setSummary(res.summary)
      })
      .catch(() => {
        if (!cancelled) setError('血缘数据加载失败，请确认后端已运行 ETL cross_validate_jds')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [params])

  // 修改过滤条件时回到第一页（事件处理内重置，避免 effect 内同步 setState）
  function onFilterChange(next: Partial<{ q: string; onlyVerified: boolean; onlyLowConfidence: boolean }>) {
    if (next.q !== undefined) setQ(next.q)
    if (next.onlyVerified !== undefined) setOnlyVerified(next.onlyVerified)
    if (next.onlyLowConfidence !== undefined) setOnlyLowConfidence(next.onlyLowConfidence)
    setPage(1)
  }

  async function openDetail(item: LineagePositionItem) {
    setDetailLoading(true)
    setDetailError(null)
    try {
      const res = await apiGet<LineageDetail>(
        `/admin/lineage/positions/${encodeURIComponent(item.position_name)}`,
      )
      setDetail(res)
    } catch {
      setDetailError('详情加载失败，请重试')
      setDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }

  return (
    <>
      <PageHeader
        title="数据血缘"
        description="岗位/技能声明 ← 抽取证据 ← 原始 JD 记录 · 跨源印证与溯源跳转（P13 管理端可视化）"
      />

      {/* 血缘总览统计（真实 lineage_summary） */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <Network className="size-4 text-ink-faint" />
              <span className="text-xs font-mono text-ink-muted">GROUPS</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">
              {summary?.groups ?? '—'}
            </div>
            <div className="text-xs text-ink-muted mt-1">岗位分组数</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <Database className="size-4 text-ink-faint" />
              <span className="text-xs font-mono text-ink-muted">JD</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">
              {summary?.jd_count?.toLocaleString() ?? '—'}
            </div>
            <div className="text-xs text-ink-muted mt-1">覆盖证据 JD</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <FileSearch className="size-4 text-ink-faint" />
              <span className="text-xs font-mono text-ink-muted">MULTI</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">
              {summary?.multi_source ?? '—'}
            </div>
            <div className="text-xs text-ink-muted mt-1">≥2 独立源印证</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <ShieldCheck className="size-4 text-ink-faint" />
              <span className="text-xs font-mono text-ink-muted">OK</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">
              {summary?.verified ?? '—'}
            </div>
            <div className="text-xs text-ink-muted mt-1">已验证（跨源）</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <TriangleAlert className="size-4 text-ink-faint" />
              <span className="text-xs font-mono text-ink-muted">&lt;0.6</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">
              {summary?.below_confidence ?? '—'}
            </div>
            <div className="text-xs text-ink-muted mt-1">低置信待复核</div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center justify-between">
            <span>血缘岗位列表</span>
            <span className="text-xs font-normal text-ink-faint">共 {total} 个岗位分组</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {/* 过滤条 */}
          <div className="flex flex-wrap items-center gap-2 mb-4">
            <Input
              value={q}
              onChange={(e) => onFilterChange({ q: e.target.value })}
              placeholder="按岗位名关键字过滤"
              className="w-full sm:w-56 h-8 text-sm"
            />
            <Button
              size="sm"
              variant={onlyVerified ? 'default' : 'outline'}
              className="h-8 text-xs"
              onClick={() => onFilterChange({ onlyVerified: !onlyVerified })}
            >
              仅已验证
            </Button>
            <Button
              size="sm"
              variant={onlyLowConfidence ? 'default' : 'outline'}
              className="h-8 text-xs"
              onClick={() => onFilterChange({ onlyLowConfidence: !onlyLowConfidence })}
            >
              仅低置信（&lt;0.6）
            </Button>
          </div>

          {loading ? (
            <p className="py-12 text-center text-sm text-ink-muted">加载血缘数据…</p>
          ) : error ? (
            <p className="py-12 text-center text-sm text-state-archived">{error}</p>
          ) : items.length === 0 ? (
            <div className="py-12 text-center text-sm text-ink-faint">
              <p>暂无血缘分组</p>
              <p className="text-xs mt-2">
                血缘数据由 ETL cross_validate_jds 按已抽取 jd_raw 记录生成，冷启动阶段可能为空（属预期）
              </p>
            </div>
          ) : isDesktop ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>岗位名</TableHead>
                  <TableHead className="text-center">证据 JD</TableHead>
                  <TableHead className="text-center">数据源</TableHead>
                  <TableHead>城市</TableHead>
                  <TableHead className="text-right">置信度</TableHead>
                  <TableHead>技能印证</TableHead>
                  <TableHead>薪资</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={item.position_name}>
                    <TableCell className="font-medium max-w-48 truncate">{item.position_name}</TableCell>
                    <TableCell className="text-center tabular-nums font-mono">{item.jd_count}</TableCell>
                    <TableCell className="text-center">
                      <Badge variant={item.source_count >= 2 ? 'stable' : 'candidate'} className="text-[11px]">
                        {item.source_count} 源
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-ink-muted max-w-40 truncate">
                      {(item.cities ?? []).join(' / ') || '—'}
                    </TableCell>
                    <TableCell className="text-right">
                      <span className={`font-mono tabular-nums text-sm ${confidenceTone(item.confidence)}`}>
                        {Math.round(item.confidence * 100)}%
                      </span>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {item.verified ? (
                          <Badge variant="outline" className="text-[11px] text-state-stable border-state-stable/30">
                            印证 {(item.verified_skill_ratio * 100).toFixed(0)}%
                          </Badge>
                        ) : (
                          warnBadge('candidate', '单源待审')
                        )}
                        {item.salary_outlier && warnBadge('declining', '薪资异常')}
                        {item.experience_divergence > 0.5 && warnBadge('declining', '经验分歧')}
                        {item.confidence < 0.6 && warnBadge('declining', '低置信')}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs font-mono text-ink-muted">
                      {item.salary_median ? `${Math.round(item.salary_median).toLocaleString()}/月` : '—'}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" variant="ghost" onClick={() => openDetail(item)}>
                        溯源
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <div className="space-y-3">
              {items.map((item) => (
                <div key={item.position_name} className="rounded-lg border border-border p-3 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-sm text-ink truncate">{item.position_name}</span>
                    <span className={`font-mono tabular-nums text-sm ${confidenceTone(item.confidence)}`}>
                      {Math.round(item.confidence * 100)}%
                    </span>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-ink-muted">
                    <Badge variant={item.source_count >= 2 ? 'stable' : 'candidate'} className="text-[11px]">
                      {item.source_count} 源
                    </Badge>
                    <span>JD {item.jd_count}</span>
                    <span className="truncate">{(item.cities ?? []).join(' / ') || '—'}</span>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {item.verified ? (
                      <Badge variant="outline" className="text-[11px] text-state-stable border-state-stable/30">
                        印证 {(item.verified_skill_ratio * 100).toFixed(0)}%
                      </Badge>
                    ) : (
                      warnBadge('candidate', '单源待审')
                    )}
                    {item.salary_outlier && warnBadge('declining', '薪资异常')}
                    {item.experience_divergence > 0.5 && warnBadge('declining', '经验分歧')}
                    {item.confidence < 0.6 && warnBadge('declining', '低置信')}
                  </div>
                  <div className="flex items-center justify-between gap-2 pt-1 border-t border-border">
                    <span className="text-xs font-mono text-ink-muted">
                      {item.salary_median ? `${Math.round(item.salary_median).toLocaleString()}/月` : '—'}
                    </span>
                    <Button size="sm" variant="ghost" onClick={() => openDetail(item)}>
                      溯源
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <PaginationBar
            page={page}
            total={total}
            pageSize={PAGE_SIZE}
            loading={loading}
            onPageChange={setPage}
          />
        </CardContent>
      </Card>

      {/* 详情弹窗：组级校验 + 证据 JD 血缘链明细（溯源到原始来源） */}
      <Dialog
        open={detail !== null || detailLoading || detailError !== null}
        onOpenChange={(o) => {
          if (!o) {
            setDetail(null)
            setDetailError(null)
          }
        }}
      >
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>
              数据血缘 · {detail?.position_name ?? (detailLoading ? '加载中…' : '')}
            </DialogTitle>
            <DialogDescription>
              组级跨源校验 + 组内每条证据 JD（溯源到原始招聘来源）
            </DialogDescription>
          </DialogHeader>

          {detailLoading ? (
            <p className="py-8 text-center text-sm text-ink-muted">加载血缘详情…</p>
          ) : detailError ? (
            <p className="py-8 text-center text-sm text-state-archived">{detailError}</p>
          ) : detail ? (
            <div className="space-y-4">
              {/* 组级校验摘要 */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="rounded-md bg-subtle p-3">
                  <div className="text-xs text-ink-muted mb-1">数据源</div>
                  <div className="font-mono text-sm">{detail.source_count} 个</div>
                  <div className="text-[11px] text-ink-faint mt-0.5">{(detail.sources ?? []).join('、')}</div>
                </div>
                <div className="rounded-md bg-subtle p-3">
                  <div className="text-xs text-ink-muted mb-1">跨源置信度</div>
                  <div className={`font-mono text-sm ${confidenceTone(detail.confidence)}`}>
                    {Math.round(detail.confidence * 100)}%
                  </div>
                  <div className="text-[11px] text-ink-faint mt-0.5">
                    印证 {Math.round((detail.verified_skill_ratio ?? 0) * 100)}%
                  </div>
                </div>
                <div className="rounded-md bg-subtle p-3">
                  <div className="text-xs text-ink-muted mb-1">市场月薪（跨城市平滑）</div>
                  <div className="font-mono text-sm">
                    {detail.salary_median ? `${Math.round(detail.salary_median).toLocaleString()} 元` : '—'}
                  </div>
                  <div className="text-[11px] text-ink-faint mt-0.5">
                    {detail.salary_outlier ? '跨平台差异 >50%' : '口径一致'}
                  </div>
                </div>
                <div className="rounded-md bg-subtle p-3">
                  <div className="text-xs text-ink-muted mb-1">经验分歧度</div>
                  <div className="font-mono text-sm">{detail.experience_divergence.toFixed(2)}</div>
                  <div className="text-[11px] text-ink-faint mt-0.5">
                    {detail.experience_divergence > 0.5 ? '跨平台分歧' : '基本一致'}
                  </div>
                </div>
              </div>

              {/* 单源技能（未达 2 源印证，待人工审核） */}
              {(detail.unverified_skills ?? []).length > 0 && (
                <div className="rounded-md border border-state-candidate/20 bg-state-candidate/5 p-3">
                  <div className="text-xs text-state-candidate mb-1">单源技能（未达 2 源印证，待人工审核）</div>
                  <div className="flex flex-wrap gap-1">
                    {(detail.unverified_skills ?? []).map((s) => (
                      <Badge key={s} variant="outline" className="text-[11px]">{s}</Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* 证据 JD 血缘链明细 */}
              <div>
                <div className="text-xs text-ink-muted mb-2">
                  证据 JD 血缘链（{(detail.records ?? []).length} 条，按入库序）
                </div>
                <div className="max-h-[45vh] overflow-auto rounded-md border border-border">
                {isDesktop ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>来源</TableHead>
                      <TableHead>城市</TableHead>
                      <TableHead>薪资</TableHead>
                      <TableHead>采集时间</TableHead>
                      <TableHead>技能</TableHead>
                      <TableHead className="text-center">去重</TableHead>
                      <TableHead className="text-right">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(detail.records ?? []).map((rec) => (
                      <TableRow key={rec.jd_id}>
                        <TableCell>
                          <Badge variant="outline" className="text-[11px]">{rec.source || '—'}</Badge>
                        </TableCell>
                        <TableCell className="text-xs text-ink-muted">{rec.city || '—'}</TableCell>
                        <TableCell className="text-xs font-mono text-ink-secondary max-w-32 truncate">
                          {rec.salary || '—'}
                        </TableCell>
                        <TableCell className="text-xs font-mono text-ink-muted">
                          {rec.crawled_at ? formatDateTime(rec.crawled_at) : '—'}
                        </TableCell>
                        <TableCell className="text-xs text-ink-muted max-w-48 truncate">
                          {(rec.skills ?? []).join('、') || '—'}
                        </TableCell>
                        <TableCell className="text-center">
                          {rec.is_duplicate ? (
                            <Badge variant="archived" className="text-[11px]">重复</Badge>
                          ) : (
                            <span className="text-xs text-ink-faint">—</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          {rec.source_url ? (
                            <a
                              href={rec.source_url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-xs text-ink underline hover:no-underline"
                            >
                              原始 JD ↗
                            </a>
                          ) : (
                            <span className="text-xs text-ink-faint">无 URL</span>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                    {(detail.records ?? []).length === 0 && (
                      <TableRow>
                        <TableCell colSpan={7} className="text-center text-sm text-ink-faint py-6">
                          组内无证据 JD 记录
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
                ) : (
                  <div className="space-y-2 p-2">
                    {(detail.records ?? []).length === 0 ? (
                      <p className="text-center text-sm text-ink-faint py-6">组内无证据 JD 记录</p>
                    ) : (detail.records ?? []).map((rec) => (
                      <div key={rec.jd_id} className="rounded-md border border-border p-2.5 space-y-1.5">
                        <div className="flex items-center justify-between gap-2">
                          <Badge variant="outline" className="text-[11px]">{rec.source || '—'}</Badge>
                          {rec.is_duplicate ? (
                            <Badge variant="archived" className="text-[11px]">重复</Badge>
                          ) : null}
                        </div>
                        <div className="flex items-center gap-3 text-xs text-ink-muted">
                          <span>{rec.city || '—'}</span>
                          <span className="font-mono">{rec.salary || '—'}</span>
                        </div>
                        <div className="text-[11px] text-ink-faint font-mono">
                          {rec.crawled_at ? formatDateTime(rec.crawled_at) : '—'}
                        </div>
                        {(rec.skills ?? []).length > 0 && (
                          <div className="flex flex-wrap gap-1">
                            {(rec.skills ?? []).map((s) => (
                              <span key={s} className="rounded bg-subtle px-1 py-0.5 text-[10px] text-ink-muted">{s}</span>
                            ))}
                          </div>
                        )}
                        {rec.source_url ? (
                          <a
                            href={rec.source_url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-xs text-ink underline hover:no-underline"
                          >
                            原始 JD ↗
                          </a>
                        ) : (
                          <span className="text-xs text-ink-faint">无 URL</span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                </div>
              </div>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  )
}
