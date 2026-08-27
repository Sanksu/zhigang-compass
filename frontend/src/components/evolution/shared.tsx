/** 演化视图组件（从 evolution-page.tsx 抽出，第六轮审查拆分：页面 ≤800 行惯例）。 */
import { useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import type { MetricItem } from './types'

export function SearchableSelect({
  value,
  placeholder,
  options,
  loading,
  onSearch,
  onSelect,
  pageSize,
}: {
  value: string
  placeholder: string
  options: { value: string; label: string }[]
  loading?: boolean
  onSearch?: (q: string) => void
  onSelect: (v: string) => void
  /** 选项分页（10 项一页，08-16 用户决策：版本对比下拉翻页浏览） */
  pageSize?: number
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [optionPage, setOptionPage] = useState(1)
  const timerRef = useRef<number | null>(null)
  const current = options.find((o) => o.value === value)
  const ql = q.trim().toLowerCase()
  const filtered = ql
    ? options.filter((o) => o.label.toLowerCase().includes(ql))
    : options
  const totalPages = pageSize ? Math.max(1, Math.ceil(filtered.length / pageSize)) : 1
  const visible = pageSize
    ? filtered.slice((optionPage - 1) * pageSize, optionPage * pageSize)
    : filtered

  return (
    <div className="relative">
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-8 w-48 justify-start font-mono text-xs"
        onClick={() => {
          setOpen((v) => !v)
          setQ('')
          setOptionPage(1)
        }}
      >
        <span className="truncate text-ink">{current?.label || placeholder}</span>
      </Button>
      {open && (
        <>
          {/* 点击外部关闭 */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute z-50 mt-1 w-64 rounded-md border border-border bg-elevated shadow-lg">
            <div className="p-1.5">
              <Input
                autoFocus
                value={q}
                placeholder="输入名称搜索…"
                className="h-7 text-xs"
                onChange={(e) => {
                  const v = e.target.value
                  setQ(v)
                  setOptionPage(1)
                  if (!onSearch) return
                  if (timerRef.current) window.clearTimeout(timerRef.current)
                  timerRef.current = window.setTimeout(() => onSearch(v.trim()), 300)
                }}
              />
            </div>
            <div className="max-h-64 overflow-auto p-1">
              {loading ? (
                <p className="px-2 py-3 text-center text-xs text-ink-faint">搜索中…</p>
              ) : visible.length === 0 ? (
                <p className="px-2 py-3 text-center text-xs text-ink-faint">无匹配结果</p>
              ) : (
                visible.map((o) => (
                  <button
                    key={o.value}
                    type="button"
                    className={cn(
                      'block w-full truncate rounded px-2 py-1.5 text-left text-xs hover:bg-subtle',
                      o.value === value && 'bg-subtle font-medium',
                    )}
                    onClick={() => {
                      onSelect(o.value)
                      setOpen(false)
                    }}
                  >
                    {o.label}
                  </button>
                ))
              )}
            </div>
            {/* 选项分页（10 项一页，08-16 用户决策） */}
            {pageSize && totalPages > 1 && (
              <div className="flex items-center justify-between border-t border-border px-2 py-1.5">
                <span className="text-[10px] text-ink-faint">
                  第 {Math.min(optionPage, totalPages)} / {totalPages} 页 · 共 {filtered.length} 个
                </span>
                <div className="flex items-center gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 px-2 text-[10px]"
                    disabled={optionPage <= 1}
                    onClick={() => setOptionPage((p) => Math.max(1, p - 1))}
                  >
                    上一页
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 px-2 text-[10px]"
                    disabled={optionPage >= totalPages}
                    onClick={() => setOptionPage((p) => Math.min(totalPages, p + 1))}
                  >
                    下一页
                  </Button>
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}


// ===== MetricCard =====

export function MetricCard({ metric }: { metric: MetricItem }) {
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

