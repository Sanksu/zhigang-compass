import { useEffect, useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { apiGet } from '@/lib/api'
import type { Schema } from './review-types'

type WatchRow = Schema['WatchItem']

const WATCH_SOURCE_LABEL: Record<string, string> = {
  jd: 'JD',
  arxiv: '论文',
  course: '课程',
  github: 'GitHub',
  community: '社区',
  stackoverflow: 'SO',
}

/**
 * 发现观察池 Tab — 设计文档 §7.2.5（admin 周报可见）
 *
 * 数据源：真实 GET /admin/discovery/watch（技术热点信号列表，支持按 status/source 筛选）。
 * 展示技能信号周报：信号源 / 信号值 / 周期 / 状态（watch / candidate_promoted / archived）。
 */
export function TechnologyWatchTab() {
  const [items, setItems] = useState<WatchRow[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState('')

  const PAGE_SIZE = 50
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const load = (status = '', source = '', p = 1) => {
    const params = new URLSearchParams({ page: String(p), size: String(PAGE_SIZE) })
    if (status) params.set('status', status)
    if (source) params.set('source', source)
    apiGet<Schema['WatchData']>(`/admin/discovery/watch?${params}`)
      .then((res) => {
        setItems(res.items)
        setTotal(res.total)
      })
      .catch(() => setError('观察池加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [])

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Select value={statusFilter} onValueChange={(v) => { setStatusFilter(v); setPage(1); load(v, sourceFilter, 1) }}>
          <SelectTrigger className="w-36 h-8 text-xs">
            <SelectValue placeholder="状态筛选" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">全部状态</SelectItem>
            <SelectItem value="watch">观察中</SelectItem>
            <SelectItem value="candidate_promoted">候选提升</SelectItem>
            <SelectItem value="archived">已归档</SelectItem>
          </SelectContent>
        </Select>
        <Select value={sourceFilter} onValueChange={(v) => { setSourceFilter(v); setPage(1); load(statusFilter, v, 1) }}>
          <SelectTrigger className="w-32 h-8 text-xs">
            <SelectValue placeholder="来源筛选" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">全部来源</SelectItem>
            {Object.entries(WATCH_SOURCE_LABEL).map(([k, v]) => (
              <SelectItem key={k} value={k}>{v}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-xs text-ink-muted">共 {total} 条信号（技术热点周报 · 设计文档 §7.2.5）</span>
      </div>

      {error ? (
        <Card>
          <CardContent className="py-8 text-center text-xs text-state-archived">{error}</CardContent>
        </Card>
      ) : loading ? (
        <Card>
          <CardContent className="py-8 text-center text-xs text-ink-faint">加载观察池…</CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            {items.length === 0 ? (
              <p className="py-10 text-center text-xs text-ink-faint">暂无观察池信号（依赖每日观察池任务）</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>技能</TableHead>
                    <TableHead>信号源</TableHead>
                    <TableHead>信号值</TableHead>
                    <TableHead>周期</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>最近信号</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((w, i) => (
                    <TableRow key={`${w.skill_name}-${w.signal_source}-${w.period}-${i}`}>
                      <TableCell className="font-medium text-ink">{w.skill_name}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-[11px] font-mono">
                          {WATCH_SOURCE_LABEL[w.signal_source] ?? w.signal_source}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono tabular-nums text-ink-muted">
                        {typeof w.signal_value === 'number' ? w.signal_value.toFixed(3) : w.signal_value}
                      </TableCell>
                      <TableCell className="text-xs text-ink-muted font-mono">{w.period}</TableCell>
                      <TableCell>
                        <Badge
                          variant={w.status === 'candidate_promoted' ? 'emerging' : w.status === 'archived' ? 'archived' : 'outline'}
                          className="text-[11px]"
                        >
                          {w.status === 'candidate_promoted' ? '候选提升' : w.status === 'archived' ? '已归档' : '观察中'}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-ink-faint font-mono">
                        {w.last_signal_at ? w.last_signal_at.slice(0, 16).replace('T', ' ') : '—'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            {/* 翻页：总条数 > 每页 50 时出现（后端 /admin/discovery/watch 已分页） */}
            {total > PAGE_SIZE && (
              <div className="flex items-center justify-between border-t border-border px-4 py-2.5">
                <span className="text-xs text-ink-muted">
                  第 {page} / {totalPages} 页 · 每页 {PAGE_SIZE} 条
                </span>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 px-2.5 text-xs"
                    disabled={page <= 1 || loading}
                    onClick={() => { const p = page - 1; setPage(p); load(statusFilter, sourceFilter, p) }}
                  >
                    上一页
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 px-2.5 text-xs"
                    disabled={page >= totalPages || loading}
                    onClick={() => { const p = page + 1; setPage(p); load(statusFilter, sourceFilter, p) }}
                  >
                    下一页
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
