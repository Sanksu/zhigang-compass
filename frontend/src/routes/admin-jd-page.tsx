import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { ExternalLink, FileText, Flag, Loader2 } from 'lucide-react'
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
 *  - 人工复核闭环：质量分 <0.6 的 JD（needs_review）被抽取游标跳过，可按
 *    「只看待复核」筛选，在弹窗内核对正文后「放行」——后端撤销 skipped 标记，
 *    该行重新进入抽取队列，下轮 ETL 批抽调用 LLM 抽取并入图
 *  - 删除：写审计日志，图谱 Evidence 备份不联动删除
 */
export function AdminJdPage() {
  const [items, setItems] = useState<JdAdminItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // ?q= 预填（原始数据页 JD tab 点行跳转携带标题）
  const [searchParams] = useSearchParams()
  const [q, setQ] = useState(searchParams.get('q') ?? '')
  const [source, setSource] = useState('')
  // 人工复核队列筛选：true 时请求带 needs_review=true（质量分 <0.6 待复核）
  const [pendingOnly, setPendingOnly] = useState(false)
  // 检索防抖值（第八轮 P2-31）：输入停止 300ms 后才提交给列表请求，
  // 避免逐键触发 /admin/jd 查询；初始值与空输入一致，不影响首屏加载
  // 预填词直接进入防抖值，首屏即按该词过滤（300ms 防抖只对后续输入生效）
  const [debouncedQ, setDebouncedQ] = useState(searchParams.get('q') ?? '')
  const [debouncedSource, setDebouncedSource] = useState('')
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQ(q.trim())
      setDebouncedSource(source.trim())
    }, 300)
    return () => clearTimeout(timer)
  }, [q, source])
  /** 删除后手动触发列表重查（递增即 refetch） */
  const [reloadKey, setReloadKey] = useState(0)

  /* 详情/编辑弹窗：detailSeq 时序守卫（第七轮 P1-5）——慢响应不覆盖新选择，
     已关闭弹窗的迟到响应不重新弹开；detailError 列表级展示（P1-6，
     加载失败时弹窗条件不成立，弹窗内错误会被吞掉） */
  const [detail, setDetail] = useState<JdAdminDetail | null>(null)
  const [form, setForm] = useState<JdEditForm | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const detailSeqRef = useRef(0)
  const [saving, setSaving] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  /* 放行（needs_review true→false）：行级按钮走二次确认弹窗，弹窗内按钮直发 */
  const [releaseTarget, setReleaseTarget] = useState<JdAdminItem | null>(null)
  const [releasing, setReleasing] = useState(false)
  const [releaseError, setReleaseError] = useState<string | null>(null)

  const params = useMemo(() => {
    const p = new URLSearchParams({ page: String(page), size: String(PAGE_SIZE) })
    if (debouncedQ) p.set('q', debouncedQ)
    if (debouncedSource) p.set('source', debouncedSource)
    if (pendingOnly) p.set('needs_review', 'true')
    return p.toString()
  }, [debouncedQ, debouncedSource, pendingOnly, page])

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
    const seq = ++detailSeqRef.current
    setDetailLoading(true)
    setDetailError(null)
    setActionError(null)
    try {
      const res = await apiGet<JdAdminDetail>(`/admin/jd/${item.id}`)
      if (seq !== detailSeqRef.current) return // 迟到响应：已有更新选择或已关闭
      setDetail(res)
      setForm(toForm(res))
    } catch {
      if (seq === detailSeqRef.current) setDetailError('详情加载失败，请重试')
    } finally {
      if (seq === detailSeqRef.current) setDetailLoading(false)
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

  /** 放行：撤销 skipped 抽取标记重新进入抽取队列；详情弹窗开着时同步刷新详情 */
  async function release(jdId: number) {
    setReleasing(true)
    setReleaseError(null)
    try {
      const res = await apiPut<JdAdminDetail>(`/admin/jd/${jdId}`, { needs_review: false })
      setReleaseTarget(null)
      if (detail?.id === jdId) {
        setDetail(res)
        setForm(toForm(res))
      }
      refresh()
    } catch {
      setReleaseError('放行失败，请重试')
    } finally {
      setReleasing(false)
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
                  <TableHead className="text-center">质量/复核</TableHead>
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
                    <TableCell className="text-center">
                      <div className="flex items-center justify-center gap-1.5">
                        <span className="font-mono text-xs tabular-nums text-ink-muted">
                          {item.quality != null ? item.quality.toFixed(2) : '—'}
                        </span>
                        {item.needs_review && (
                          <Badge variant="archived" className="text-[11px]">待复核</Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-center font-mono text-xs tabular-nums">
                      {(item.text_length ?? 0).toLocaleString()}
                    </TableCell>
                    <TableCell className="text-xs font-mono text-ink-muted">
                      {item.crawled_at?.slice(0, 10) || '—'}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        {item.needs_review && (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={releasing}
                            onClick={() => setReleaseTarget(item)}
                          >
                            放行
                          </Button>
                        )}
                        <Button size="sm" variant="ghost" onClick={() => openDetail(item)}>
                          编辑
                        </Button>
                      </div>
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
            detailSeqRef.current++ // 作废在途请求：迟到响应不得重新弹开已关闭弹窗
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
                {detail.needs_review && (
                  <Badge variant="archived" className="text-[11px]">
                    待人工复核（质量 {detail.quality != null ? detail.quality.toFixed(2) : '—'}）
                  </Badge>
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
                {detail.needs_review && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={releasing}
                    onClick={() => release(detail.id)}
                  >
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

      {/* 放行二次确认（行级入口；详情弹窗内按钮直发不走此处） */}
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
