import { type CSSProperties, type ElementType, type ReactNode, useEffect, useState } from 'react'
import { cn } from '@/lib/utils'

/** 挂载即触发的入场（fade-in + 轻微上移），非滚动回显。
 *  用于「页面内容分级入场」：整页进入时按块错峰浮现，视觉上有层级节奏。
 *  尊重 prefers-reduced-motion（globals.css 已全局把动画时长压到 0.01ms）。 */
export function Reveal({
  as: Tag = 'div',
  className,
  delay = 0,
  style,
  children,
  ...rest
}: {
  as?: ElementType
  className?: string
  /** 入场延迟（ms）——Stagger 用 index×step 驱动错峰；独立使用时可按需给固定值 */
  delay?: number
  style?: CSSProperties
  children?: ReactNode
}) {
  const [entered, setEntered] = useState(false)
  useEffect(() => {
    // 下一帧再切换 class：先保持 opacity-0 占位，再触发 animate-in（避免首帧闪现）
    const raf = requestAnimationFrame(() => setEntered(true))
    return () => cancelAnimationFrame(raf)
  }, [])
  return (
    <Tag
      className={cn(
        entered
          ? 'animate-in slide-in-from-bottom-2 fade-in-0 fill-mode-backwards duration-400 ease-out'
          : 'opacity-0',
        className,
      )}
      style={{ ...style, animationDelay: delay ? `${delay}ms` : undefined }}
      {...rest}
    >
      {children}
    </Tag>
  )
}

/**
 * 分级入场容器：把每个直接子元素包一层 Reveal，按 index×step 依次浮现。
 * 适用于**块级竖直堆叠**的内容（卡片 / 列表项 / 分栏区块）——foreach 出的
 * 网格单元请直接用 `<Reveal>` 当单元本身（包一层会破坏 grid 单元布局）。
 */
export function Stagger({
  children,
  step = 70,
  from = 0,
  className,
}: {
  children?: ReactNode
  /** 相邻项入场间隔（ms） */
  step?: number
  /** 首项延迟起点（ms） */
  from?: number
  className?: string
}) {
  const items = Array.isArray(children) ? children : [children]
  // 仅对真实元素计步（跳过空/null/布尔），index 由 filter 后的 map 提供，render 期无变量改写
  const realItems = items.filter((child) => child !== null && typeof child !== 'boolean')
  return (
    <div className={className}>
      {realItems.map((child, i) => (
        <Reveal key={`reveal-${i}-${from + i * step}`} delay={from + i * step}>
          {child}
        </Reveal>
      ))}
    </div>
  )
}