/**
 * 侧边详情面板响应式容器 — 多端响应式（task T4）
 *
 * 同一份 Tab 内容（由父级以 children 传入）做两套布局，CSS 断点二选一显示：
 * - lg 及以上：右侧固定 320px 侧栏（现状）
 * - lg 以下：底部抽屉 Bottom Sheet，选中节点自动唤起，可关闭回全屏画布；
 *   遮罩点击/关闭时回调关闭（不遮挡画布是移动端核心诉求）。
 *
 * 注意：children 在两棵 React 子树中各渲染一次（桌面侧栏 + 移动抽屉），
 * 用 CSS 隐藏其一 —— 换取布局一致与组件单一来源，非"零重复渲染"。
 * 状态仍上抛给父级（graph-page），本容器只负责布局与开合。
 */
import { useCallback, useEffect, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

type TabKey = 'detail' | 'analysis'

interface GraphDetailRailProps {
  rightTab: TabKey
  onRightTabChange: (v: TabKey) => void
  ready: boolean
  onClose: () => void
  children: ReactNode
  className?: string
}

/** 纯内容（不含抽屉骨架）——侧栏与底部抽屉共用，保证桌面/移动一致 */
export function GraphDetailTabs({
  rightTab,
  onRightTabChange,
  children,
}: {
  rightTab: TabKey
  onRightTabChange: (v: TabKey) => void
  children: React.ReactNode
}) {
  return (
    <Tabs value={rightTab} onValueChange={(v) => onRightTabChange(v as TabKey)} className="flex size-full flex-col">
      <TabsList className="mx-3 mt-3 grid w-auto grid-cols-2">
        <TabsTrigger value="detail" className="text-xs">
          节点详情
        </TabsTrigger>
        <TabsTrigger value="analysis" className="text-xs">
          算法分析
        </TabsTrigger>
      </TabsList>
      {children}
    </Tabs>
  )
}

export function GraphDetailRail({
  rightTab,
  onRightTabChange,
  ready,
  onClose,
  children,
  className,
}: GraphDetailRailProps) {
  // 浏览器 back / ESC 关闭抽屉（移动端返回键惯例）
  const handleKey = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape' && ready) onClose()
    },
    [ready, onClose],
  )
  useEffect(() => {
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [handleKey])

  // 打开时锁定背景滚动（移动端抽屉常见做法，避免底层画布误触滚动）
  useEffect(() => {
    const prev = document.body.style.overflow
    if (ready) document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [ready])

  return (
    <>
      {/* ── 移动端底部抽屉（lg 以下；ready=false 时收起） ── */}
      <div className="fixed inset-0 z-50 lg:hidden" aria-hidden={!ready}>
        {/* 半透明遮罩：点击关闭，回看画布 */}
        <div
          className={cn(
            'absolute inset-0 bg-black/40 backdrop-blur-sm transition-opacity duration-300',
            ready ? 'opacity-100' : 'pointer-events-none opacity-0',
          )}
          onClick={onClose}
        />
        {/* 抽屉主体：底部上滑 */}
        <div
          className={cn(
            'absolute inset-x-0 bottom-0 flex max-h-[82vh] flex-col rounded-t-2xl border-t border-border bg-canvas shadow-2xl transition-transform duration-300 ease-out',
            ready ? 'translate-y-0' : 'translate-y-full',
          )}
        >
          {/* 拖拽把手 */}
          <div className="flex justify-center pt-2">
            <div className="h-1 w-10 rounded-full bg-border" />
          </div>
          {/* 抽屉头部：标题占位 + 关闭 */}
          <div className="flex items-center justify-between border-b border-border/60 px-4 py-2">
            <span className="text-xs font-medium text-ink-muted">详情</span>
            <Button size="sm" variant="ghost" className="h-7 w-7 p-0" onClick={onClose} aria-label="关闭详情抽屉">
              <X className="size-4" />
            </Button>
          </div>
          {/* 抽屉内容：溢出滚动 */}
          <div className="min-h-0 flex-1 overflow-y-auto">
            <GraphDetailTabs rightTab={rightTab} onRightTabChange={onRightTabChange}>
              {children}
            </GraphDetailTabs>
          </div>
        </div>
      </div>

      {/* ── 桌面侧栏（lg 及以上，保持现状；ready=false 时也保留骨架） ── */}
      <Card className={cn('hidden h-full flex-col overflow-hidden lg:flex lg:h-[640px]', className)}>
        <GraphDetailTabs rightTab={rightTab} onRightTabChange={onRightTabChange}>
          {children}
        </GraphDetailTabs>
      </Card>
    </>
  )
}