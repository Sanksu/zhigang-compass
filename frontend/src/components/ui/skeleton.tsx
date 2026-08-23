/**
 * 骨架屏基础组件 — 纯 CSS pulse 动画，无额外依赖。
 * 自 node-detail-panel 私有实现提升为共享（AI 生成感加载统一使用）。
 */
import { cn } from '@/lib/utils'

/** 骨架屏行 */
export function SkeletonLine({ className }: { className?: string }) {
  return <div className={cn('h-7 rounded-lg bg-subtle animate-pulse', className)} />
}

const LIST_WIDTHS = ['w-full', 'w-4/5', 'w-3/5', 'w-11/12', 'w-2/3']

/** 骨架屏列表（宽度递减模拟文本段落） */
export function SkeletonList({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn('space-y-2', className)}>
      {Array.from({ length: rows }, (_, i) => (
        <SkeletonLine key={i} className={LIST_WIDTHS[i % LIST_WIDTHS.length]} />
      ))}
    </div>
  )
}
