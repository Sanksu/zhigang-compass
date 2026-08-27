/**
 * 岗位状态徽标 — 六态徽标的唯一源。
 *
 * 契约：PositionNode.status 状态机 = active/candidate/emerging/stable/declining/archived
 * （openapi.yaml GraphNode.status，active=图谱常态岗位，非发现状态机成员）；
 * rejected 为流转展示态（审核驳回，复用归档视觉）。
 * 收敛前 5 处重复定义（resume-match / node-detail-panel / graph 图例 /
 * evolution state-views / discovery）到此组件。
 */
import { Badge, type BadgeProps } from '@/components/ui/badge'

export type PositionState =
  | 'active'
  | 'candidate'
  | 'emerging'
  | 'stable'
  | 'declining'
  | 'archived'
  | 'rejected'

export const POSITION_STATE_META: Record<
  PositionState,
  { label: string; variant: NonNullable<BadgeProps['variant']> }
> = {
  active: { label: '活跃', variant: 'active' },
  candidate: { label: '候选', variant: 'candidate' },
  emerging: { label: '新兴', variant: 'emerging' },
  stable: { label: '稳定', variant: 'stable' },
  declining: { label: '衰退', variant: 'declining' },
  archived: { label: '归档', variant: 'archived' },
  rejected: { label: '驳回', variant: 'archived' },
}

interface PositionStateBadgeProps extends Omit<BadgeProps, 'variant'> {
  /** 岗位状态键（未知值回退为 outline + 原文） */
  state: string
  /** 覆盖默认中文标签（如流转记录需展示原始 state 串时传入） */
  label?: string
  /** 覆盖默认 variant */
  variant?: BadgeProps['variant']
}

export function PositionStateBadge({
  state,
  label,
  variant,
  ...rest
}: PositionStateBadgeProps) {
  const meta =
    POSITION_STATE_META[state as PositionState] ?? { label: state, variant: 'outline' }
  return (
    <Badge variant={variant ?? meta.variant} {...rest}>
      {label ?? meta.label}
    </Badge>
  )
}