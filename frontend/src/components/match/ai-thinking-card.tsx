/**
 * AI 生成中占位卡 — 替代裸 spinner 的加载呈现。
 *
 * Sparkles 呼吸 + 骨架屏 shimmer + 分阶段状态文案轮播（每条至少停留
 * STAGE_MS，覆盖 LLM 多通道切换的真实等待节奏）；prefers-reduced-motion
 * 用户仅显示静态文案（无轮播、无闪烁）。
 */
import { useEffect, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { SkeletonList } from '@/components/ui/skeleton'
import { cn, prefersReducedMotion } from '@/lib/utils'

/** 每条阶段文案的最短停留时长 */
const STAGE_MS = 6000

interface AiThinkingCardProps {
  /** 阶段文案序列（随耗时推进轮播，最后一条停留） */
  stages: string[]
  /** 骨架行数（默认 3） */
  rows?: number
  /** 底部静态说明（如实告知预期等待，如「LLM 推理约需 1 分钟」） */
  hint?: string
  className?: string
}

export function AiThinkingCard({ stages, rows = 3, hint, className }: AiThinkingCardProps) {
  const [stageIndex, setStageIndex] = useState(0)
  const reduced = prefersReducedMotion()

  useEffect(() => {
    if (reduced || stages.length <= 1) return
    const id = window.setInterval(() => {
      setStageIndex((i) => Math.min(i + 1, stages.length - 1))
    }, STAGE_MS)
    return () => window.clearInterval(id)
  }, [reduced, stages.length])

  return (
    <div className={cn('py-8', className)}>
      <div className="mb-4 flex items-center gap-2">
        <Sparkles className="size-4 shrink-0 animate-pulse text-accent" />
        <p className="text-xs font-medium text-ink">
          {stages[stageIndex]}
          {!reduced && <span className="ml-0.5 inline-block h-3 w-[2px] animate-pulse bg-ink align-middle" />}
        </p>
      </div>
      <SkeletonList rows={rows} />
      {hint && <p className="mt-3 text-[11px] text-ink-faint">{hint}</p>}
    </div>
  )
}
