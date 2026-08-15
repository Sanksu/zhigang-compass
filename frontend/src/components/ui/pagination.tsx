/**
 * 通用分页条（演化看板 4 视图共用，2026-08-16）。
 *
 * 展示「第 P / N 页 · 共 X 条」+ 上一页/下一页（边界/加载中禁用）。
 * 页数 = 1 时不渲染（单页无需翻页）。
 */
import { Button } from '@/components/ui/button'

export interface PaginationBarProps {
  page: number
  total: number
  pageSize: number
  /** 加载中禁用翻页按钮（防连点重复请求） */
  loading?: boolean
  onPageChange: (page: number) => void
}

export function PaginationBar({ page, total, pageSize, loading, onPageChange }: PaginationBarProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  if (totalPages <= 1) return null

  return (
    <div className="flex items-center justify-between border-t border-border px-4 py-2.5">
      <span className="text-xs text-ink-muted">
        第 {page} / {totalPages} 页 · 共 {total} 条 · 每页 {pageSize} 条
      </span>
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          className="h-7 px-2.5 text-xs"
          disabled={page <= 1 || loading}
          onClick={() => onPageChange(page - 1)}
        >
          上一页
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-7 px-2.5 text-xs"
          disabled={page >= totalPages || loading}
          onClick={() => onPageChange(page + 1)}
        >
          下一页
        </Button>
      </div>
    </div>
  )
}
