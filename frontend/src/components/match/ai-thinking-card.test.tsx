/**
 * AiThinkingCard 渲染单测 — 阶段文案/骨架行/静态说明的呈现契约。
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AiThinkingCard } from './ai-thinking-card'

describe('AiThinkingCard', () => {
  it('渲染首条阶段文案与底部静态说明', () => {
    render(<AiThinkingCard stages={['AI 正在生成诊断报告…']} hint="LLM 推理约需 1 分钟" />)
    expect(screen.getByText(/AI 正在生成诊断报告/)).toBeInTheDocument()
    expect(screen.getByText('LLM 推理约需 1 分钟')).toBeInTheDocument()
  })

  it('多阶段文案初始仅显示第一条', () => {
    render(<AiThinkingCard stages={['阶段一文案', '阶段二文案', '阶段三文案']} />)
    expect(screen.getByText(/阶段一文案/)).toBeInTheDocument()
    expect(screen.queryByText(/阶段二文案/)).not.toBeInTheDocument()
  })

  it('骨架行按 rows 数量渲染', () => {
    const { container } = render(<AiThinkingCard stages={['加载中…']} rows={4} />)
    // SkeletonList 内 4 个 SkeletonLine（animate-pulse 骨架块）
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThanOrEqual(4)
  })
})
