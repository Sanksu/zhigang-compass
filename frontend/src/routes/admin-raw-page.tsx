import { useEffect, useMemo, useState } from 'react'
import { ExternalLink, Loader2, Trash2 } from 'lucide-react'
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
import { useNavigate } from 'react-router'
import { apiDelete, apiGet, apiPut } from '@/lib/api'
import type { components } from '@/types/api'

type RawAdminPage = components['schemas']['RawAdminPage']
type RawAdminItem = components['schemas']['RawAdminItem']
type JdAdminItem = components['schemas']['JdAdminItem']
type RawAdminDetail = components['schemas']['RawAdminDetail']

const PAGE_SIZE = 20

type RawType = 'jd' | 'course' | 'paper' | 'community'

interface ExtraColumn {
  key: string
  label: string
  render?: (v: unknown) => string
}

const num = (v: unknown) => (v == null ? '—' : String(v))

/** 每个 tab 的类型特有列（extra 由后端 _extra_fields 计算） */
const TAB_CONFIG: Record<RawType, { label: string; sourcePlaceholder: string; extraColumns: ExtraColumn[] }> = {
  jd: {
    label: 'JD',
    sourcePlaceholder: '来源（zhilian/boss…）',
    // JD 特有口径（needs_review 复核放行/归一化岗位/质量筛选）在专用 JD 数据页，
    // 本 tab 仅列表检索，点行跳转 /admin/jd 操作
    extraColumns: [],
  },
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

/** 编辑表单状态（受控；空串表示清空该字段） */
interface RawEditForm {
  title: string
  source_url: string
  raw_text: string
}

function toForm(detail: RawAdminDetail): RawEditForm {
  return {
    title: detail.title ?? '',
    source_url: detail.source_url ?? '',
    raw_text: detail.raw_text ?? '',
  }
}

/**
 * 原始数据管理页 — 课程/论文/社区信号三类 raw 表治理入口（jd_raw 走专用 JD 数据页）
 *
 * 与 JD 数据页同能力口径（拍板 1-B）：列表检索（关键字/来源）、详情编辑
 * （title/raw_text/source_url）、删除；全部变更写审计日志。编辑随既有 ETL
 * 指纹重算自然生效，图谱侧节点为独立备份不联动。
 */
export function AdminRawPage() {
  const navigate = useNavigate()
  const [rawType, setRawType] = useState<RawType>('jd')
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

  const [reloadKey, setReloadKey] = useState(0)

  const [detail, setDetail] = useState<RawAdminDetail | null>(null)
  const [form, setForm] = useState<RawEditForm | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<RawAdminItem | null>(null)

  const params = useMemo(() => {
    const p = new URLSearchParams({ page: String(page), size: String(PAGE_SIZE) })
    if (debouncedQ) p.set('q', debouncedQ)
    if (debouncedSource) p.set('source', debouncedSource)
    return p.toString()
  }, [debouncedQ, debouncedSource, page])

  function refresh() {
    setReloadKey((k) => k + 1)
  }

  useEffect(() => {
    let cancelled = false
    // loading 置位在 tab 切换的事件处理器中完成（lint：effect 内同步 setState
    // 触发级联渲染）；本 effect 只负责回落
    // jd tab 复用 jd_admin 端点（needs_review/position 等字段为 RawAdminItem 展示面的超集，
    // 类型此处窄化统一；其余 tab 走通用 raw 端点）
    const url = rawType === 'jd' ? `/admin/jd?${params}` : `/admin/raw/${rawType}?${params}`
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
  }, [rawType, params, reloadKey])

  async function openDetail(item: RawAdminItem) {
    setDetailLoading(true)
    setDetailError(null)
    setActionError(null)
    try {
      const res = await apiGet<RawAdminDetail>(`/admin/raw/${rawType}/${item.id}`)
      setDetail(res)
      setForm(toForm(res))
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
      await apiPut(`/admin/raw/${rawType}/${detail.id}`, form)
      setDetail(null)
      setForm(null)
      refresh()
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
      await apiDelete(`/admin/raw/${rawType}/${id}`)
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

  const dirty = detail && form && JSON.stringify(toForm(detail)) !== JSON.stringify(form)
  const conf = TAB_CONFIG[rawType]

  return (
    <>
      <PageHeader
        title="原始数据管理"
        description="JD / 课程 / 论文 / 社区信号四类 raw 表治理：列表检索 / 标题与出处编辑 / 删除（全部变更写审计日志；JD 详情编辑与复核放行在 JD 数据页）"
      />

      <Card>
        <CardContent className="pt-4">
          <Tabs value={rawType} onValueChange={(v) => { setRawType(v as RawType); setPage(1); setSource(''); setQ(''); setLoading(true) }}>
            <TabsList className="mb-4">
              {(Object.keys(TAB_CONFIG) as RawType[]).map((t) => (
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
              placeholder={conf.sourcePlaceholder}
              className="w-44 h-8 text-sm"
            />
            <span className="text-xs font-normal text-ink-faint">共 {total} 条</span>
          </div>

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
            <p className="py-12 text-center text-sm text-ink-faint">暂无{conf.label}记录</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-16 text-center">ID</TableHead>
                  <TableHead>标题</TableHead>
                  <TableHead className="text-center">来源</TableHead>
                  {conf.extraColumns.map((col) => (
                    <TableHead key={col.key} className="text-center">{col.label}</TableHead>
                  ))}
                  <TableHead>采集时间</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => {
                  const extra = ((item as RawAdminItem).extra ?? {}) as Record<string, unknown>
                  return (
                    <TableRow key={item.id}>
                      <TableCell className="text-center font-mono text-xs text-ink-muted tabular-nums">
                        {item.id}
                      </TableCell>
                      <TableCell className="font-medium max-w-56 truncate">
                        {item.title || '—'}
                      </TableCell>
                      <TableCell className="text-center">
                        <Badge variant="outline" className="text-[11px]">{item.source || '—'}</Badge>
                      </TableCell>
                      {conf.extraColumns.map((col) => (
                        <TableCell key={col.key} className="text-center text-xs text-ink-muted">
                          {col.render
                            ? col.render(extra[col.key])
                            : extra[col.key] != null
                              ? String(extra[col.key])
                              : '—'}
                        </TableCell>
                      ))}
                      <TableCell className="text-xs font-mono text-ink-muted">
                        {item.crawled_at?.slice(0, 10) || '—'}
                      </TableCell>
                      <TableCell className="text-right">
                        {rawType === 'jd' ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 text-xs text-state-candidate"
                            onClick={() => navigate(`/admin/jd?q=${encodeURIComponent(item.title || '')}`)}
                          >
                            去 JD 数据页
                          </Button>
                        ) : (
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
                                setDeleteTarget(item)
                              }}
                            >
                              <Trash2 className="size-3.5" />
                            </Button>
                          </div>
                        )}
                      </TableCell>
                    </TableRow>
                  )
                })}
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
            <DialogTitle>
              {detail ? `${conf.label} #${detail.id} · ${detail.source}` : `${conf.label}详情`}
            </DialogTitle>
            <DialogDescription>
              编辑标题/出处/正文；编辑随既有 ETL 指纹重算自然生效，图谱节点不联动
            </DialogDescription>
          </DialogHeader>

          {detailLoading ? (
            <p className="py-8 text-center text-sm text-ink-muted">加载详情…</p>
          ) : detail && form ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <label className="space-y-1 flex-1 min-w-64">
                  <span className="text-[11px] text-ink-muted">标题（title）</span>
                  <Input
                    className="h-8 text-sm"
                    value={form.title}
                    onChange={(e) => setForm({ ...form, title: e.target.value })}
                  />
                </label>
                <div className="flex items-center gap-2 pt-4">
                  {detail.is_desensitized && (
                    <Badge variant="archived" className="text-[11px]">已脱敏</Badge>
                  )}
                  {detail.source_url && (
                    <a
                      href={detail.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs text-ink underline hover:no-underline"
                    >
                      打开原文 <ExternalLink className="inline size-3" />
                    </a>
                  )}
                </div>
              </div>
              <label className="block space-y-1">
                <span className="text-[11px] text-ink-muted">出处链接（source_url）</span>
                <Input
                  className="h-8 text-sm"
                  value={form.source_url}
                  onChange={(e) => setForm({ ...form, source_url: e.target.value })}
                />
              </label>

              <label className="block space-y-1">
                <span className="text-[11px] text-ink-muted">正文（raw_text）</span>
                <textarea
                  className="h-[32vh] w-full resize-y rounded-md border border-border bg-canvas p-2.5 text-[13px] leading-relaxed text-ink-secondary focus:outline-none focus:ring-1 focus:ring-primary"
                  value={form.raw_text}
                  onChange={(e) => setForm({ ...form, raw_text: e.target.value })}
                />
              </label>

              {actionError && <p className="text-xs text-state-archived">{actionError}</p>}

              <DialogFooter className="gap-2">
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={saving}
                  onClick={() => setConfirmDelete(true)}
                >
                  <Trash2 className="mr-1 size-3.5" />
                  删除
                </Button>
                <Button size="sm" disabled={!dirty || saving} onClick={save}>
                  {saving && <Loader2 className="mr-1 size-3 animate-spin" />}
                  {dirty ? '保存变更' : '已保存'}
                </Button>
              </DialogFooter>
            </div>
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
              确认删除{conf.label} #{deleteTarget?.id ?? detail?.id}？
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
    </>
  )
}
