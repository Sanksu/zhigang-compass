import { useEffect, useMemo, useState } from 'react'
import { ExternalLink, FileText, Flag, Loader2, Trash2 } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { PaginationBar } from '@/components/ui/pagination'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
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
import { apiDelete, apiGet, apiPut } from '@/lib/api'
import type { components } from '@/types/api'

type RawAdminPage = components['schemas']['RawAdminPage']
type RawAdminItem = components['schemas']['RawAdminItem']
type JdAdminItem = components['schemas']['JdAdminItem']
type JdAdminDetail = components['schemas']['JdAdminDetail']
type RawAdminDetail = components['schemas']['RawAdminDetail']

const PAGE_SIZE = 20

type RawType = 'jd' | 'course' | 'paper' | 'community'

interface ExtraColumn {
  key: string
  label: string
  render?: (v: unknown) => string
}

const num = (v: unknown) => (v == null ? '—' : String(v))

/** 每个 tab 的类型特有列（extra 由后端 _extra_fields 计算；JD 另有专属列走 /admin/jd） */
const TAB_CONFIG: Record<Exclude<RawType, 'jd'>, { label: string; sourcePlaceholder: string; extraColumns: ExtraColumn[] }> = {
  course: {
    label: '课程',
    sourcePlaceholder: '来源（icourse163）',
    extraColumns: [
      { key: 'quality', label: '质量', render: (v) => (v == null ? '—' : Number(v).toFixed(2)) },
      { key: 'skills_count', label: '技能数', render: num },
      { key: 'institution', label: '机构', render: (v) => (v ? String(v) : '—') },
    ],
  },
  paper: {
    label: '论文',
    sourcePlaceholder: '来源（arxiv）',
    extraColumns: [
      { key: 'published', label: '发表', render: (v) => (v ? String(v) : '—') },
      { key: 'authors_count', label: '作者数', render: num },
    ],
  },
  community: {
    label: '社区信号',
    sourcePlaceholder: '来源（github）',
    extraColumns: [
      { key: 'stars', label: 'Stars', render: num },
      { key: 'votes', label: 'Votes', render: num },
      { key: 'trend_type', label: '趋势', render: (v) => (v ? String(v) : '—') },
    ],
  },
}

/** 编辑表单状态（受控；空串表示清空该字段）——JD 专属字段多，与通用异构 */
interface JdEditForm {
  title: string
  company: string
  location: string
  source_url: string
  crawled_at: string
  raw_text: string
}

interface RawEditForm {
  title: string
  source_url: string
  raw_text: string
}

type EditForm = JdEditForm | RawEditForm

function toJdForm(detail: JdAdminDetail): JdEditForm {
  return {
    title: detail.title ?? '',
    company: detail.company ?? '',
    location: detail.location ?? '',
    source_url: detail.source_url ?? '',
    crawled_at: detail.crawled_at ?? '',
    raw_text: detail.raw_text ?? '',
  }
}

function toRawForm(detail: RawAdminDetail): RawEditForm {
  return {
    title: detail.title ?? '',
    source_url: detail.source_url ?? '',
    raw_text: detail.raw_text ?? '',
  }
}

/** 按详情异构类型换算编辑表单（JD 与通用表单字段数不同） */
function detailToForm(detail: JdAdminDetail | RawAdminDetail): EditForm {
  return 'raw_type' in detail ? toRawForm(detail) : toJdForm(detail)
}

/**
 * 原始数据管理页 — JD/课程/论文/社区信号四类 raw 表治理统一入口
 *
 * 与 1-B 拍板同能力口径：列表检索（关键字/来源）+ 详情编辑 + 删除，全部变更写审计日志。
 *  - 课程/论文/社区信号走通用端点 /admin/raw/{raw_type}（详情编辑 title/source_url/raw_text）
 *  - JD 复用专属端点 /admin/jd：额外提供归一化岗位列、质量/复核列、"只看待复核"筛选、
 *    行内放行（质量门 <0.6 撤销 skipped 重新进入抽取队列）、详情编辑多字段
 *    （公司/城市/采集时间/正文，正文变更同步重算 content_hash），抽取快照只读展示
 *  - 编辑随既有 ETL 指纹重算自然生效，图谱侧节点为独立备份不联动
 */
export function AdminRawPage() {
  const [rawType, setRawType] = useState<RawType>('jd')
  const isJd = rawType === 'jd'
  const [items, setItems] = useState<RawAdminItem[] | JdAdminItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [q, setQ] = useState('')
  const [source, setSource] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')
  const [debouncedSource, setDebouncedSource] = useState('')
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQ(q.trim())
      setDebouncedSource(source.trim())
    }, 300)
    return () => clearTimeout(timer)
  }, [q, source])

  // JD 专属：仅对待复核（质量 <0.6）行的复核队列筛选
  const [pendingOnly, setPendingOnly] = useState(false)

  const [reloadKey, setReloadKey] = useState(0)

  const [detail, setDetail] = useState<JdAdminDetail | RawAdminDetail | null>(null)
  const [form, setForm] = useState<EditForm | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<RawAdminItem | null>(null)
  /* JD 放行（needs_review true→false）：行级按钮走二次确认弹窗 */
  const [releaseTarget, setReleaseTarget] = useState<JdAdminItem | null>(null)
  const [releasing, setReleasing] = useState(false)
  const [releaseError, setReleaseError] = useState<string | null>(null)

  const params = useMemo(() => {
    const p = new URLSearchParams({ page: String(page), size: String(PAGE_SIZE) })
    if (debouncedQ) p.set('q', debouncedQ)
    if (debouncedSource) p.set('source', debouncedSource)
    if (isJd && pendingOnly) p.set('needs_review', 'true')
    return p.toString()
  }, [debouncedQ, debouncedSource, pendingOnly, page, isJd])

  function refresh() {
    setReloadKey((k) => k + 1)
  }

  useEffect(() => {
    let cancelled = false
    // loading 置位在 tab 切换的事件处理器中完成（lint：effect 内同步 setState
    // 触发级联渲染）；本 effect 只负责回落
    const url = isJd ? `/admin/jd?${params}` : `/admin/raw/${rawType}?${params}`
    apiGet<RawAdminPage>(url)
      .then((res) => {
        if (cancelled) return
        setError(null)
        setItems(res.items)
        setTotal(res.total)
      })
      .catch(() => {
        if (!cancelled) setError('原始数据加载失败，请稍后重试')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [isJd, rawType, params, reloadKey])

  async function openDetail(item: RawAdminItem | JdAdminItem) {
    setDetailLoading(true)
    setDetailError(null)
    setActionError(null)
    try {
      if (isJd) {
        const res = await apiGet<JdAdminDetail>(`/admin/jd/${item.id}`)
        setDetail(res)
        setForm(toJdForm(res))
      } else {
        const res = await apiGet<RawAdminDetail>(`/admin/raw/${rawType}/${item.id}`)
        setDetail(res)
        setForm(toRawForm(res))
      }
    } catch {
      setDetailError('详情加载失败，请重试')
    } finally {
      setDetailLoading(false)
    }
  }

  async function save() {
    if (!detail || !form) return
    setSaving(true)
    setActionError(null)
    try {
      if (isJd) {
        const res = await apiPut<JdAdminDetail>(`/admin/jd/${detail.id}`, form)
        setDetail(res)
        setForm(toJdForm(res))
      } else {
        await apiPut(`/admin/raw/${rawType}/${detail.id}`, form)
        setDetail(null)
        setForm(null)
        refresh()
      }
    } catch {
      setActionError('保存失败，请重试')
    } finally {
      setSaving(false)
    }
  }

  async function remove(id: number) {
    setSaving(true)
    setActionError(null)
    try {
      if (isJd) {
        await apiDelete(`/admin/jd/${id}`)
      } else {
        await apiDelete(`/admin/raw/${rawType}/${id}`)
      }
      setConfirmDelete(false)
      setDeleteTarget(null)
      setDetail(null)
      setForm(null)
      refresh()
    } catch {
      setActionError('删除失败，请重试')
    } finally {
      setSaving(false)
    }
  }

  /** JD 放行：撤销 skipped 抽取标记重新进入抽取队列；详情弹窗开着时同步刷新详情 */
  async function release(jdId: number) {
    setReleasing(true)
    setReleaseError(null)
    try {
      const res = await apiPut<JdAdminDetail>(`/admin/jd/${jdId}`, { needs_review: false })
      setReleaseTarget(null)
      const d = detail
      if (d && !('snapshot' in d) && d.id === jdId) {
        setDetail(res)
        setForm(toJdForm(res))
      }
      refresh()
    } catch {
      setReleaseError('放行失败，请重试')
    } finally {
      setReleasing(false)
    }
  }

  const conf = (isJd ? null : TAB_CONFIG[rawType]) as typeof TAB_CONFIG[keyof typeof TAB_CONFIG] | null
  const dirty = detail && form && JSON.stringify(detailToForm(detail)) !== JSON.stringify(form)

  return (
    <>
      <PageHeader
        title="原始数据管理"
        description="JD / 课程 / 论文 / 社区信号四类 raw 表治理：列表检索 / 标题与出处编辑 / 删除（全部变更写审计日志；JD 附归一化岗位、质量复核与放行）"
      />

      <Card>
        <CardContent className="pt-4">
          <Tabs value={rawType} onValueChange={(v) => { setRawType(v as RawType); setPage(1); setSource(''); setQ(''); setLoading(true) }}>
            <TabsList className="mb-4">
              <TabsTrigger value="jd" className="text-xs">JD</TabsTrigger>
              {(Object.keys(TAB_CONFIG) as Exclude<RawType, 'jd'>[]).map((t) => (
                <TabsTrigger key={t} value={t} className="text-xs">
                  {TAB_CONFIG[t].label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>

          <div className="flex flex-wrap items-center gap-2 mb-4">
            <Input
              value={q}
              onChange={(e) => {
                setQ(e.target.value)
                setPage(1)
              }}
              placeholder="按标题/正文关键字过滤"
              className="w-56 h-8 text-sm"
            />
            <Input
              value={source}
              onChange={(e) => {
                setSource(e.target.value)
                setPage(1)
              }}
              placeholder={isJd ? '来源平台（如 zhilian）' : conf?.sourcePlaceholder}
              className="w-44 h-8 text-sm"
            />
            {isJd && (
              <Button
                size="sm"
                variant={pendingOnly ? 'default' : 'outline'}
                className="h-8 text-sm"
                onClick={() => {
                  setPendingOnly(!pendingOnly)
                  setPage(1)
                }}
              >
                <Flag className="mr-1 size-3.5" />
                只看待复核
              </Button>
            )}
            <span className="text-xs font-normal text-ink-faint">共 {total} 条</span>
          </div>

          {releaseError && (
            <p className="mb-3 rounded-md border border-state-archived/30 bg-state-archived/5 px-3 py-2 text-xs text-state-archived">
              {releaseError}
            </p>
          )}

          {detailError && (
            <p className="mb-3 rounded-md border border-state-archived/30 bg-state-archived/5 px-3 py-2 text-xs text-state-archived">
              {detailError}
            </p>
          )}

          {loading ? (
            <p className="py-12 text-center text-sm text-ink-muted">加载中…</p>
          ) : error ? (
            <p className="py-12 text-center text-sm text-state-archived">{error}</p>
          ) : items.length === 0 ? (
            <p className="py-12 text-center text-sm text-ink-faint">暂无{isJd ? 'JD' : conf?.label}记录</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-16 text-center">ID</TableHead>
                  {isJd ? (
                    <>
                      <TableHead>标题</TableHead>
                      <TableHead>公司</TableHead>
                      <TableHead className="text-center">来源</TableHead>
                      <TableHead>归一化岗位</TableHead>
                      <TableHead className="text-center">质量/复核</TableHead>
                      <TableHead className="text-center">正文字数</TableHead>
                      <TableHead>采集时间</TableHead>
                    </>
                  ) : (
                    <>
                      <TableHead>标题</TableHead>
                      <TableHead className="text-center">来源</TableHead>
                      {conf?.extraColumns.map((col) => (
                        <TableHead key={col.key} className="text-center">{col.label}</TableHead>
                      ))}
                      <TableHead>采集时间</TableHead>
                    </>
                  )}
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) =>
                  isJd ? (
                    (() => {
                      const jd = item as JdAdminItem
                      return (
                        <TableRow key={jd.id}>
                          <TableCell className="text-center font-mono text-xs text-ink-muted tabular-nums">{jd.id}</TableCell>
                          <TableCell className="font-medium max-w-52 truncate">{jd.title || '—'}</TableCell>
                          <TableCell className="text-xs text-ink-muted max-w-32 truncate">{jd.company || '—'}</TableCell>
                          <TableCell className="text-center">
                            <Badge variant="outline" className="text-[11px]">{jd.source || '—'}</Badge>
                          </TableCell>
                          <TableCell className="text-xs text-ink-muted max-w-36 truncate">{jd.position || '—'}</TableCell>
                          <TableCell className="text-center">
                            <div className="flex items-center justify-center gap-1.5">
                              <span className="font-mono text-xs tabular-nums text-ink-muted">
                                {jd.quality != null ? jd.quality.toFixed(2) : '—'}
                              </span>
                              {jd.needs_review && (
                                <Badge variant="archived" className="text-[11px]">待复核</Badge>
                              )}
                            </div>
                          </TableCell>
                          <TableCell className="text-center font-mono text-xs tabular-nums">
                            {(jd.text_length ?? 0).toLocaleString()}
                          </TableCell>
                          <TableCell className="text-xs font-mono text-ink-muted">
                            {jd.crawled_at?.slice(0, 10) || '—'}
                          </TableCell>
                          <TableCell className="text-right">
                            <div className="flex items-center justify-end gap-1">
                              {jd.needs_review && (
                                <Button size="sm" variant="ghost" disabled={releasing} onClick={() => setReleaseTarget(jd)}>
                                  放行
                                </Button>
                              )}
                              <Button size="sm" variant="ghost" onClick={() => openDetail(jd)}>
                                编辑
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      )
                    })()
                  ) : (
                    (() => {
                      const extra = ((item as RawAdminItem).extra ?? {}) as Record<string, unknown>
                      return (
                        <TableRow key={item.id}>
                          <TableCell className="text-center font-mono text-xs text-ink-muted tabular-nums">{item.id}</TableCell>
                          <TableCell className="font-medium max-w-56 truncate">{item.title || '—'}</TableCell>
                          <TableCell className="text-center">
                            <Badge variant="outline" className="text-[11px]">{item.source || '—'}</Badge>
                          </TableCell>
                          {conf?.extraColumns.map((col) => (
                            <TableCell key={col.key} className="text-center text-xs text-ink-muted">
                              {col.render ? col.render(extra[col.key]) : extra[col.key] != null ? String(extra[col.key]) : '—'}
                            </TableCell>
                          ))}
                          <TableCell className="text-xs font-mono text-ink-muted">{item.crawled_at?.slice(0, 10) || '—'}</TableCell>
                          <TableCell className="text-right">
                            <div className="flex items-center justify-end gap-1">
                              <Button size="sm" variant="ghost" onClick={() => openDetail(item)}>
                                编辑
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                className="text-state-archived"
                                onClick={() => {
                                  setActionError(null)
                                  setDeleteTarget(item as RawAdminItem)
                                }}
                              >
                                <Trash2 className="size-3.5" />
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      )
                    })()
                  ),
                )}
              </TableBody>
            </Table>
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

      {/* 详情/编辑弹窗 */}
      <Dialog
        open={detail !== null || detailLoading}
        onOpenChange={(o) => {
          if (!o) {
            setDetail(null)
            setForm(null)
            setActionError(null)
            setDetailError(null)
            setConfirmDelete(false)
          }
        }}
      >
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {isJd && <FileText className="size-4 text-ink-muted" />}
              {detail
                ? `${isJd ? 'JD' : conf?.label} #${detail.id} · ${detail.source}`
                : `${isJd ? 'JD' : conf?.label}详情`}
            </DialogTitle>
            <DialogDescription>
              编辑标题/出处/正文；编辑随既有 ETL 指纹重算自然生效，图谱节点不联动
              {isJd && '；抽取快照不自动重抽，重抽需放行后由 ETL 触发'}
            </DialogDescription>
          </DialogHeader>

          {detailLoading ? (
            <p className="py-8 text-center text-sm text-ink-muted">加载详情…</p>
          ) : detail && form ? (
            isJd ? (
              (() => {
                const d = detail as JdAdminDetail
                const f = form as JdEditForm
                return (
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                      <label className="space-y-1">
                        <span className="text-[11px] text-ink-muted">标题</span>
                        <Input className="h-8 text-sm" value={f.title} onChange={(e) => setForm({ ...f, title: e.target.value })} />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-ink-muted">公司</span>
                        <Input className="h-8 text-sm" value={f.company} onChange={(e) => setForm({ ...f, company: e.target.value })} />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-ink-muted">城市</span>
                        <Input className="h-8 text-sm" value={f.location} onChange={(e) => setForm({ ...f, location: e.target.value })} />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[11px] text-ink-muted">采集时间</span>
                        <Input className="h-8 text-sm" value={f.crawled_at} onChange={(e) => setForm({ ...f, crawled_at: e.target.value })} />
                      </label>
                      <label className="col-span-2 space-y-1">
                        <span className="text-[11px] text-ink-muted">出处链接（source_url）</span>
                        <div className="flex items-center gap-2">
                          <Input className="h-8 text-sm flex-1" value={f.source_url} onChange={(e) => setForm({ ...f, source_url: e.target.value })} />
                          {d.source_url && (
                            <a href={d.source_url} target="_blank" rel="noreferrer" className="shrink-0 text-xs text-ink underline hover:no-underline">
                              打开 <ExternalLink className="inline size-3" />
                            </a>
                          )}
                        </div>
                      </label>
                    </div>

                    {/* 抽取摘要（只读） */}
                    <div className="flex flex-wrap gap-1.5">
                      <Badge variant="outline" className="text-[11px]">
                        抽取 · {d.extraction_summary?.salary_range || '薪资未解析'}
                      </Badge>
                      <Badge variant="outline" className="text-[11px]">
                        {d.extraction_summary?.education_level || '学历未标注'}
                      </Badge>
                      {d.extraction_summary?.experience && (
                        <Badge variant="outline" className="text-[11px]">
                          经验 {d.extraction_summary.experience}
                        </Badge>
                      )}
                      {d.is_desensitized && (
                        <Badge variant="archived" className="text-[11px]">已脱敏</Badge>
                      )}
                      {d.needs_review && (
                        <Badge variant="archived" className="text-[11px]">
                          待人工复核（质量 {d.quality != null ? d.quality.toFixed(2) : '—'}）
                        </Badge>
                      )}
                    </div>

                    <label className="block space-y-1">
                      <span className="text-[11px] text-ink-muted">正文（raw_text）</span>
                      <textarea
                        className="h-[35vh] w-full resize-y rounded-md border border-border bg-canvas p-2.5 text-[13px] leading-relaxed text-ink-secondary focus:outline-none focus:ring-1 focus:ring-primary"
                        value={f.raw_text}
                        onChange={(e) => setForm({ ...f, raw_text: e.target.value })}
                      />
                    </label>

                    {actionError && <p className="text-xs text-state-archived">{actionError}</p>}

                    <DialogFooter className="gap-2">
                      <Button size="sm" variant="destructive" disabled={saving} onClick={() => setConfirmDelete(true)}>
                        删除
                      </Button>
                      {d.needs_review && (
                        <Button size="sm" variant="outline" disabled={releasing} onClick={() => release(d.id)}>
                          {releasing && <Loader2 className="mr-1 size-3 animate-spin" />}
                          放行（重新抽取）
                        </Button>
                      )}
                      <Button size="sm" disabled={!dirty || saving} onClick={save}>
                        {saving && <Loader2 className="mr-1 size-3 animate-spin" />}
                        {dirty ? '保存变更' : '已保存'}
                      </Button>
                    </DialogFooter>
                  </div>
                )
              })()
            ) : (
              (() => {
                const f = form as RawEditForm
                return (
                  <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <label className="space-y-1 flex-1 min-w-64">
                        <span className="text-[11px] text-ink-muted">标题（title）</span>
                        <Input className="h-8 text-sm" value={f.title} onChange={(e) => setForm({ ...f, title: e.target.value })} />
                      </label>
                      <div className="flex items-center gap-2 pt-4">
                        {detail.is_desensitized && <Badge variant="archived" className="text-[11px]">已脱敏</Badge>}
                        {detail.source_url && (
                          <a href={detail.source_url} target="_blank" rel="noreferrer" className="text-xs text-ink underline hover:no-underline">
                            打开原文 <ExternalLink className="inline size-3" />
                          </a>
                        )}
                      </div>
                    </div>
                    <label className="block space-y-1">
                      <span className="text-[11px] text-ink-muted">出处链接（source_url）</span>
                      <Input className="h-8 text-sm" value={f.source_url} onChange={(e) => setForm({ ...f, source_url: e.target.value })} />
                    </label>
                    <label className="block space-y-1">
                      <span className="text-[11px] text-ink-muted">正文（raw_text）</span>
                      <textarea
                        className="h-[32vh] w-full resize-y rounded-md border border-border bg-canvas p-2.5 text-[13px] leading-relaxed text-ink-secondary focus:outline-none focus:ring-1 focus:ring-primary"
                        value={f.raw_text}
                        onChange={(e) => setForm({ ...f, raw_text: e.target.value })}
                      />
                    </label>
                    {actionError && <p className="text-xs text-state-archived">{actionError}</p>}
                    <DialogFooter className="gap-2">
                      <Button size="sm" variant="destructive" disabled={saving} onClick={() => setConfirmDelete(true)}>
                        <Trash2 className="mr-1 size-3.5" />
                        删除
                      </Button>
                      <Button size="sm" disabled={!dirty || saving} onClick={save}>
                        {saving && <Loader2 className="mr-1 size-3 animate-spin" />}
                        {dirty ? '保存变更' : '已保存'}
                      </Button>
                    </DialogFooter>
                  </div>
                )
              })()
            )
          ) : null}
        </DialogContent>
      </Dialog>

      {/* 删除二次确认（行内入口；详情弹窗内按钮复用同一确认弹窗） */}
      <Dialog
        open={confirmDelete || deleteTarget !== null}
        onOpenChange={(o) => {
          if (!o) {
            setConfirmDelete(false)
            setDeleteTarget(null)
          }
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              确认删除{isJd ? 'JD' : conf?.label} #{deleteTarget?.id ?? detail?.id}？
            </DialogTitle>
            <DialogDescription>
              该操作写审计日志且不可撤销；图谱侧节点为独立备份不联动，但聚合与
              学习路径将不再包含此原始记录。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setConfirmDelete(false)
                setDeleteTarget(null)
              }}
            >
              取消
            </Button>
            <Button
              size="sm"
              variant="destructive"
              disabled={saving}
              onClick={() => {
                const id = deleteTarget?.id ?? detail?.id
                if (id != null) void remove(id)
              }}
            >
              {saving && <Loader2 className="mr-1 size-3 animate-spin" />}
              确认删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* JD 放行二次确认（行级入口；详情弹窗内按钮直发不走此处） */}
      <Dialog
        open={releaseTarget !== null}
        onOpenChange={(o) => {
          if (!o) setReleaseTarget(null)
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>放行 JD #{releaseTarget?.id}？</DialogTitle>
            <DialogDescription>
              该 JD 因质量分 {releaseTarget?.quality != null ? releaseTarget.quality.toFixed(2) : '—'}{' '}
              低于阈值（0.6）被跳过抽取。放行后撤销 skipped 标记、重新进入抽取队列，
              下轮 ETL 批抽将调用 LLM 抽取并入图；操作写审计日志。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button size="sm" variant="outline" onClick={() => setReleaseTarget(null)}>
              取消
            </Button>
            <Button
              size="sm"
              disabled={releasing}
              onClick={() => releaseTarget && release(releaseTarget.id)}
            >
              {releasing && <Loader2 className="mr-1 size-3 animate-spin" />}
              确认放行
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}