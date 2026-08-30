/**
 * 动态别名表（自 admin-skill-aliases-page 抽出，08-27 并入 llm-decisions「动态别名表」Tab）：
 * LLM 发现 + 人工审批回写的技能别名，approved 行即 commit 到 normalize_skill 读序
 * （词典 → 动态 → 白名单）。自持分页/状态过滤/加载。
 */
import { useEffect, useMemo, useState } from 'react'
import { ArrowRight, BookMarked } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { PaginationBar } from '@/components/ui/pagination'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { apiGet } from '@/lib/api'
import { formatDateTime } from '@/lib/utils'
import type { components } from '@/types/api'

type SkillAliasItem = components['schemas']['SkillAliasItem']

const PAGE_SIZE = 20

const STATUS_TONE: Record<string, string> = {
  pending: 'text-state-candidate',
  approved: 'text-state-stable',
  rejected: 'text-state-declining',
}

const STATUS_LABEL: Record<string, string> = {
  pending: '待审',
  approved: '已生效',
  rejected: '已驳回',
}

export function SkillAliasesTable() {
  const [items, setItems] = useState<SkillAliasItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const params = useMemo(() => {
    const p = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) })
    if (status) p.set('status', status)
    return p.toString()
  }, [status, page])

  useEffect(() => {
    let cancelled = false
    apiGet<{ items: SkillAliasItem[]; total: number; limit: number; offset: number }>(`/admin/skill-aliases?${params}`)
      .then((res) => {
        if (cancelled) return
        setError(null)
        setItems(res.items)
        setTotal(res.total)
      })
      .catch(() => {
        if (!cancelled) setError('动态别名表加载失败（别名回写审批产生数据后可见）')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [params])

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle>别名记录</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={status || 'all'}
            onValueChange={(v) => {
              setStatus(v === 'all' ? '' : v)
              setPage(1)
            }}
          >
            <SelectTrigger aria-label="状态过滤" className="h-8 w-36 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部状态</SelectItem>
              {['pending', 'approved', 'rejected'].map((s) => (
                <SelectItem key={s} value={s}>
                  {STATUS_LABEL[s] ?? s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="ghost" size="sm" onClick={() => setStatus('pending')}>
            <BookMarked className="mr-1 h-3.5 w-3.5" />
            待审批
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {error ? (
          <p className="py-8 text-center text-sm text-state-declining">{error}</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>别名变体</TableHead>
                <TableHead>归并目标</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>置信度</TableHead>
                <TableHead>审批人</TableHead>
                <TableHead>审批理由</TableHead>
                <TableHead>创建时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && items.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                    加载中…
                  </TableCell>
                </TableRow>
              ) : items.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                    暂无别名记录——审批技能名归一（别名）提案后产生
                  </TableCell>
                </TableRow>
              ) : (
                items.map((it) => (
                  <TableRow key={it.id}>
                    <TableCell className="max-w-[200px] truncate font-medium">{it.variant}</TableCell>
                    <TableCell className="max-w-[200px]">
                      <span className="flex items-center gap-1.5 truncate">
                        <ArrowRight className="h-3 w-3 shrink-0 text-state-candidate" />
                        <span className="truncate">{it.standard_name}</span>
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className={`text-[11px] ${STATUS_TONE[it.status] ?? ''}`}>
                        {STATUS_LABEL[it.status] ?? it.status}
                      </Badge>
                    </TableCell>
                    <TableCell>{it.confidence != null ? it.confidence.toFixed(2) : '-'}</TableCell>
                    <TableCell className="max-w-[120px] truncate text-xs">{it.reviewed_by || '-'}</TableCell>
                    <TableCell className="max-w-[200px] truncate text-xs text-muted-foreground">
                      {it.review_reason || '-'}
                    </TableCell>
                    <TableCell className="text-xs">{it.created_at ? formatDateTime(it.created_at) : '-'}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        )}
        <PaginationBar page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
      </CardContent>
    </Card>
  )
}