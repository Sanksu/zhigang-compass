import { useEffect, useMemo, useState } from 'react'
import { ExternalLink, FileText, Loader2 } from 'lucide-react'
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

type JdAdminPage = components['schemas']['JdAdminPage']
type JdAdminItem = components['schemas']['JdAdminItem']
type JdAdminDetail = components['schemas']['JdAdminDetail']

const PAGE_SIZE = 20

/** 编辑表单状态（受控；空串表示清空该字段） */
interface JdEditForm {
  title: string
  company: string
  location: string
  source_url: string
  crawled_at: string
  raw_text: string
}

function toForm(detail: JdAdminDetail): JdEditForm {
  return {
    title: detail.title ?? '',
    company: detail.company ?? '',
    location: detail.location ?? '',
    source_url: detail.source_url ?? '',
    crawled_at: detail.crawled_at ?? '',
    raw_text: detail.raw_text ?? '',
  }
}

/**
 * JD 数据管理页 — 岗位画像证据回溯的数据治理入口
 *
 * jd_raw 原始记录的分页列表（关键字/来源过滤）+ 详情编辑弹窗：
 *  - 编辑：标题/公司/城市/出处链接/采集时间/正文；正文或标题类字段变更时
 *    后端同步重算 content_hash（重爬重抽链路据此判定内容已变更）
 *  - 抽取快照不自动重抽（编辑不触发 LLM），抽取摘要只读展示
 *  - 删除：写审计日志，图谱 Evidence 备份不联动删除
 */
export function AdminJdPage() {
  const [items, setItems] = useState<JdAdminItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [q, setQ] = useState('')
  const [source, setSource] = useState('')
  /** 删除后手动触发列表重查（递增即 refetch） */
  const [reloadKey, setReloadKey] = useState(0)

  /* 详情/编辑弹窗 */
  const [detail, setDetail] = useState<JdAdminDetail | null>(null)
  const [form, setForm] = useState<JdEditForm | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const params = useMemo(() => {
    const p = new URLSearchParams({ page: String(page), size: String(PAGE_SIZE) })
    if (q.trim()) p.set('q', q.trim())
    if (source.trim()) p.set('source', source.trim())
    return p.toString()
  }, [q, source, page])

  function refresh() {
    setReloadKey((k) => k + 1)
  }

  useEffect(() => {
    let cancelled = false
    apiGet<JdAdminPage>(`/admin/jd?${params}`)
      .then((res) => {
        if (cancelled) return
        setError(null)
        setItems(res.items)
        setTotal(res.total)
      })
      .catch(() => {
        if (!cancelled) setError('JD 数据加载失败，请稍后重试')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [params, reloadKey])

  async function openDetail(item: JdAdminItem) {
    setDetailLoading(true)
    setActionError(null)
    try {
      const res = await apiGet<JdAdminDetail>(`/admin/jd/${item.id}`)
      setDetail(res)
      setForm(toForm(res))
    } catch {
      setActionError('详情加载失败，请重试')
    } finally {
      setDetailLoading(false)
    }
  }

  async function save() {
    if (!detail || !form) return
    setSaving(true)
    setActionError(null)
    try {
      const res = await apiPut<JdAdminDetail>(`/admin/jd/${detail.id}`, form)
      setDetail(res)
      setForm(toForm(res))
    } catch {
      setActionError('保存失败，请重试')
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    if (!detail) return
    setSaving(true)
    setActionError(null)
    try {
      await apiDelete(`/admin/jd/${detail.id}`)
      setDetail(null)
      setForm(null)
      setConfirmDelete(false)
      refresh()
    } catch {
      setActionError('删除失败，请重试')
    } finally {
      setSaving(false)
    }
  }

  const dirty = detail && form && JSON.stringify(toForm(detail)) !== JSON.stringify(form)

  return (
    <>
      <PageHeader
        title="JD 数据管理"
        description="jd_raw 原始记录治理：列表检索 / 正文与出处编辑 / 删除（全部变更写审计日志）"
      />

      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center justify-between">
            <span>原始 JD 列表</span>
            <span className="text-xs font-normal text-ink-faint">共 {total} 条</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
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
              placeholder="来源平台（如 zhilian）"
              className="w-44 h-8 text-sm"
            />
          </div>

          {loading ? (
            <p className="py-12 text-center text-sm text-ink-muted">加载 JD 数据…</p>
          ) : error ? (
            <p className="py-12 text-center text-sm text-state-archived">{error}</p>
          ) : items.length === 0 ? (
            <div className="py-12 text-center text-sm text-ink-faint">
              <p>暂无 JD 记录</p>
              <p className="text-xs mt-2">数据由采集管线写入 jd_raw，冷启动阶段可能为空（属预期）</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-16 text-center">ID</TableHead>
                  <TableHead>标题</TableHead>
                  <TableHead>公司</TableHead>
                  <TableHead className="text-center">来源</TableHead>
                  <TableHead>归一化岗位</TableHead>
                  <TableHead className="text-center">正文字数</TableHead>
                  <TableHead>采集时间</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="text-center font-mono text-xs text-ink-muted tabular-nums">
                      {item.id}
                    </TableCell>
                    <TableCell className="font-medium max-w-52 truncate">
                      {item.title || '—'}
                    </TableCell>
                    <TableCell className="text-xs text-ink-muted max-w-32 truncate">
                      {item.company || '—'}
                    </TableCell>
                    <TableCell className="text-center">
                      <Badge variant="outline" className="text-[11px]">{item.source || '—'}</Badge>
                    </TableCell>
                    <TableCell className="text-xs text-ink-muted max-w-36 truncate">
                      {item.position || '—'}
                    </TableCell>
                    <TableCell className="text-center font-mono text-xs tabular-nums">
                      {(item.text_length ?? 0).toLocaleString()}
                    </TableCell>
                    <TableCell className="text-xs font-mono text-ink-muted">
                      {item.crawled_at?.slice(0, 10) || '—'}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" variant="ghost" onClick={() => openDetail(item)}>
                        编辑
                      </Button>
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
            setConfirmDelete(false)
          }
        }}
      >
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileText className="size-4 text-ink-muted" />
              {detail ? `JD #${detail.id} · ${detail.source}` : 'JD 详情'}
            </DialogTitle>
            <DialogDescription>
              编辑正文/出处等元数据；抽取快照不自动重抽，重抽需手动触发 ETL
            </DialogDescription>
          </DialogHeader>

          {detailLoading ? (
            <p className="py-8 text-center text-sm text-ink-muted">加载详情…</p>
          ) : detail && form ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <label className="space-y-1">
                  <span className="text-[11px] text-ink-muted">标题</span>
                  <Input
                    className="h-8 text-sm"
                    value={form.title}
                    onChange={(e) => setForm({ ...form, title: e.target.value })}
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-[11px] text-ink-muted">公司</span>
                  <Input
                    className="h-8 text-sm"
                    value={form.company}
                    onChange={(e) => setForm({ ...form, company: e.target.value })}
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-[11px] text-ink-muted">城市</span>
                  <Input
                    className="h-8 text-sm"
                    value={form.location}
                    onChange={(e) => setForm({ ...form, location: e.target.value })}
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-[11px] text-ink-muted">采集时间</span>
                  <Input
                    className="h-8 text-sm"
                    value={form.crawled_at}
                    onChange={(e) => setForm({ ...form, crawled_at: e.target.value })}
                  />
                </label>
                <label className="col-span-2 space-y-1">
                  <span className="text-[11px] text-ink-muted">出处链接（source_url）</span>
                  <div className="flex items-center gap-2">
                    <Input
                      className="h-8 text-sm flex-1"
                      value={form.source_url}
                      onChange={(e) => setForm({ ...form, source_url: e.target.value })}
                    />
                    {detail.source_url && (
                      <a
                        href={detail.source_url}
                        target="_blank"
                        rel="noreferrer"
                        className="shrink-0 text-xs text-ink underline hover:no-underline"
                      >
                        打开 <ExternalLink className="inline size-3" />
                      </a>
                    )}
                  </div>
                </label>
              </div>

              {/* 抽取摘要（只读） */}
              <div className="flex flex-wrap gap-1.5">
                <Badge variant="outline" className="text-[11px]">
                  抽取 · {detail.extraction_summary?.salary_range || '薪资未解析'}
                </Badge>
                <Badge variant="outline" className="text-[11px]">
                  {detail.extraction_summary?.education_level || '学历未标注'}
                </Badge>
                {detail.extraction_summary?.experience && (
                  <Badge variant="outline" className="text-[11px]">
                    经验 {detail.extraction_summary.experience}
                  </Badge>
                )}
                {detail.is_desensitized && (
                  <Badge variant="archived" className="text-[11px]">已脱敏</Badge>
                )}
              </div>

              <label className="block space-y-1">
                <span className="text-[11px] text-ink-muted">正文（raw_text）</span>
                <textarea
                  className="h-[35vh] w-full resize-y rounded-md border border-border bg-canvas p-2.5 text-[13px] leading-relaxed text-ink-secondary focus:outline-none focus:ring-1 focus:ring-primary"
                  value={form.raw_text}
                  onChange={(e) => setForm({ ...form, raw_text: e.target.value })}
                />
              </label>

              {actionError && (
                <p className="text-xs text-state-archived">{actionError}</p>
              )}

              <DialogFooter className="gap-2">
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={saving}
                  onClick={() => setConfirmDelete(true)}
                >
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

      {/* 删除二次确认 */}
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>确认删除 JD #{detail?.id}？</DialogTitle>
            <DialogDescription>
              该操作写审计日志且不可撤销；图谱 Evidence 节点保留备份，但聚合与证据
              回溯将不再包含此记录。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button size="sm" variant="outline" onClick={() => setConfirmDelete(false)}>
              取消
            </Button>
            <Button size="sm" variant="destructive" disabled={saving} onClick={remove}>
              确认删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
