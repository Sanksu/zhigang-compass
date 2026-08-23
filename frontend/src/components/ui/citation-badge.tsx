/**
 * 引用角标（Citation Badge）— AI 推荐结论的局部溯源标签（答辩项 ③ 来源混淆 🟢）。
 *
 * 解决「部分 AI 推荐结论的局部溯源标签不够显著」：在结论旁以角标形式展示
 * 证据来源 + 置信度，替代易被忽略的纯文本 / 密集列表，让「来源混淆」显性化。
 * - 结构化证据：source / confidence / url（契约 EvidenceRef，匹配证据引用）
 * - 字符串证据：source 直接传 ref 文本（candidate 队列 evidence_refs）
 *
 * 置信度色调分级：≥0.8 强证据（绿） / ≥0.6 中证据（中性） / <0.6 弱证据（橙），
 * 无置信度时保持中性轮廓，不误导强度。
 */
import { ExternalLink } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'

export interface CitationBadgeProps {
  /** 证据来源名（智联 / O*NET / arXiv / JD-xxx…） */
  source: string
  /** 置信度 0-1（缺省不展示） */
  confidence?: number
  /** 跳转原文 URL（可选；提供时整枚角标可点击） */
  url?: string
  /** 悬停提示（溯源说明，如原始 JD 摘要） */
  title?: string
  className?: string
}

/** 置信度 → 色调分级 */
export type CitationTone = 'strong' | 'medium' | 'weak' | 'none'

export function confidenceTone(c?: number): CitationTone {
  if (c == null) return 'none'
  if (c >= 0.8) return 'strong'
  if (c >= 0.6) return 'medium'
  return 'weak'
}

const TONE_CLASS: Record<CitationTone, string> = {
  strong: 'border-state-emerging/30 bg-state-emerging/5 text-state-emerging',
  medium: 'border-border bg-subtle/50 text-ink-secondary',
  weak: 'border-state-declining/30 bg-state-declining/5 text-state-declining',
  none: 'border-border text-ink-secondary',
}

export function CitationBadge({ source, confidence, url, title, className }: CitationBadgeProps) {
  const tone = confidenceTone(confidence)
  const badge = (
    <Badge
      variant="outline"
      title={title}
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0 text-[10px] font-normal leading-4',
        TONE_CLASS[tone],
        url && 'cursor-pointer hover:bg-subtle',
        className,
      )}
    >
      <span
        className={cn(
          'size-1.5 shrink-0 rounded-full',
          tone === 'strong' ? 'bg-current' : 'bg-current opacity-40',
        )}
      />
      <span className="max-w-40 truncate">{source}</span>
      {confidence != null && (
        <span className="font-mono opacity-70">{Math.round(confidence * 100)}%</span>
      )}
      {url && <ExternalLink className="size-2.5 shrink-0 opacity-60" />}
    </Badge>
  )
  if (!url) return badge
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      title={title}
      className="inline-flex min-w-0 no-underline"
    >
      {badge}
    </a>
  )
}

/** 引用角标组：横向紧凑排列多条溯源角标，超出折叠为 +N */
export interface CitationGroupProps {
  items: CitationBadgeProps[]
  className?: string
  /** 最多展示条数（超出折叠为 +N；缺省全部展示） */
  max?: number
}

export function CitationGroup({ items, className, max }: CitationGroupProps) {
  if (items.length === 0) return null
  const rest = max && items.length > max ? items.length - max : 0
  const shown = rest > 0 ? items.slice(0, max) : items
  return (
    <div className={cn('flex flex-wrap items-center gap-1', className)}>
      {shown.map((it, i) => (
        <CitationBadge key={i} {...it} />
      ))}
      {rest > 0 && (
        <Badge variant="outline" className="rounded-full px-2 py-0 text-[10px] text-ink-faint">
          +{rest}
        </Badge>
      )}
    </div>
  )
}
