import { cn } from '@/lib/utils'

/**
 * 罗盘标记 — 智岗罗盘的签名元素
 *
 * 设计意图：
 * - "智岗罗盘" = 智慧岗位罗盘，项目名的视觉化
 * - 四向指针呼应"方向指引"——系统为求职者指明技能学习方向
 * - 主指针（北）使用墨色填充，其余描边，形成方向感
 * - active 状态下主指针使用当前路由对应的状态色
 *
 * 使用场景：
 * - TopNav 品牌标识（size=sm）
 * - Dashboard 签名展示（size=lg，配合脉冲动画）
 * - Loading 状态（spinning=true）
 */

interface CompassMarkProps {
  size?: 'sm' | 'md' | 'lg'
  spinning?: boolean
  active?: boolean
  className?: string
}

const sizeMap = {
  sm: 20,
  md: 28,
  lg: 48,
}

export function CompassMark({
  size = 'sm',
  spinning = false,
  active = false,
  className,
}: CompassMarkProps) {
  const px = sizeMap[size]
  return (
    <svg
      width={px}
      height={px}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={cn(
        'transition-transform duration-500',
        spinning && 'animate-spin',
        className,
      )}
      aria-hidden="true"
    >
      {/* 外圈 — 细线罗盘框 */}
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="1"
        className="text-border-strong"
      />
      {/* 刻度 — 四向基点 */}
      <line x1="12" y1="2" x2="12" y2="4" stroke="currentColor" strokeWidth="1" className="text-ink-faint" />
      <line x1="12" y1="20" x2="12" y2="22" stroke="currentColor" strokeWidth="1" className="text-ink-faint" />
      <line x1="2" y1="12" x2="4" y2="12" stroke="currentColor" strokeWidth="1" className="text-ink-faint" />
      <line x1="20" y1="12" x2="22" y2="12" stroke="currentColor" strokeWidth="1" className="text-ink-faint" />

      {/* 主指针 — 北向（上），填充墨色 */}
      <path
        d="M12 5 L14 12 L12 11 L10 12 Z"
        className={active ? 'fill-state-emerging' : 'fill-ink'}
      />
      {/* 副指针 — 南向（下），描边 */}
      <path
        d="M12 19 L14 12 L12 13 L10 12 Z"
        className="fill-ink-faint"
      />
      {/* 中心点 */}
      <circle cx="12" cy="12" r="1" className="fill-ink" />
    </svg>
  )
}
