/** 技能胶囊 chips — must/nice/soft 三色唯一源。
 * 收敛 resume-match / node-detail-panel 的手写技能 chips。 */
import { cn } from '@/lib/utils'

export type SkillChipTone = 'must' | 'nice' | 'soft'

/** 三色语义：必备(主色) / 加分(中性) / 软素质(粉)。均可被调用方 className 覆盖。 */
const TONE_CLASS: Record<SkillChipTone, string> = {
  must: 'border-primary/30 bg-primary/10 text-primary',
  nice: 'border-border bg-subtle text-ink-secondary',
  soft: 'border-[#ec4899]/40 bg-[#ec4899]/5 text-ink-secondary',
}

interface SkillChipProps {
  tone: SkillChipTone
  children: React.ReactNode
  onClick?: () => void
  title?: string
  className?: string
}

export function SkillChip({ tone, children, onClick, title, className }: SkillChipProps) {
  const base = cn(
    'inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[10px]',
    onClick && 'transition-colors hover:border-border-strong hover:bg-subtle/60',
    TONE_CLASS[tone],
    className,
  )
  if (!onClick) {
    return (
      <span title={title} className={base}>
        {children}
      </span>
    )
  }
  return (
    <button type="button" onClick={onClick} title={title} className={base}>
      {children}
    </button>
  )
}