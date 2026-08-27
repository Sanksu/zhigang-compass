import { cva, type VariantProps } from 'class-variance-authority'
import * as React from 'react'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium transition-colors',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-ink text-canvas',
        outline: 'border-border text-ink-secondary',
        // 岗位状态色 — 仅用于状态指示
        active: 'border-transparent bg-state-active/10 text-state-active',
        candidate: 'border-transparent bg-state-candidate/10 text-state-candidate',
        emerging: 'border-transparent bg-state-emerging/10 text-state-emerging',
        stable: 'border-transparent bg-state-stable/10 text-state-stable',
        declining: 'border-transparent bg-state-declining/10 text-state-declining',
        archived: 'border-transparent bg-state-archived/10 text-state-archived',
        // 人机协同标识 — AI 生成内容（紫）/ 人工校验确认（绿，与 emerging 同色系不同语义）
        ai: 'border-transparent bg-ai/10 text-ai',
        verified: 'border-transparent bg-state-emerging/10 text-state-emerging',
      },
    },
    defaultVariants: { variant: 'default' },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge }
