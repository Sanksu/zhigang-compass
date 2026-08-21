/**
 * CitationBadge / CitationGroup 渲染单测 — 溯源角标的呈现契约。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { CitationBadge, CitationGroup, confidenceTone } from './citation-badge'

afterEach(cleanup)

describe('confidenceTone', () => {
  it('按 0.8 / 0.6 阈值分级', () => {
    expect(confidenceTone(0.9)).toBe('strong')
    expect(confidenceTone(0.8)).toBe('strong')
    expect(confidenceTone(0.7)).toBe('medium')
    expect(confidenceTone(0.5)).toBe('weak')
    expect(confidenceTone(undefined)).toBe('none')
  })
})

describe('CitationBadge', () => {
  it('渲染来源与置信度百分比', () => {
    render(<CitationBadge source="智联" confidence={0.85} />)
    expect(screen.getByText('智联')).toBeInTheDocument()
    expect(screen.getByText('85%')).toBeInTheDocument()
  })

  it('无置信度时不渲染百分比', () => {
    render(<CitationBadge source="O*NET" />)
    expect(screen.getByText('O*NET')).toBeInTheDocument()
    expect(screen.queryByText('%')).not.toBeInTheDocument()
  })

  it('提供 url 时整枚角标可点击跳转', () => {
    render(<CitationBadge source="arXiv" confidence={0.9} url="https://example.com/paper" />)
    const link = screen.getByRole('link')
    expect(link).toHaveAttribute('href', 'https://example.com/paper')
    expect(link).toHaveAttribute('target', '_blank')
  })
})

describe('CitationGroup', () => {
  it('空数组不渲染', () => {
    const { container } = render(<CitationGroup items={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('横向排列多条角标', () => {
    render(
      <CitationGroup
        items={[
          { source: '智联', confidence: 0.9 },
          { source: 'BOSS', confidence: 0.6 },
        ]}
      />,
    )
    expect(screen.getByText('智联')).toBeInTheDocument()
    expect(screen.getByText('BOSS')).toBeInTheDocument()
  })

  it('超过 max 折叠为 +N', () => {
    render(
      <CitationGroup
        max={2}
        items={[
          { source: 'A' },
          { source: 'B' },
          { source: 'C' },
          { source: 'D' },
        ]}
      />,
    )
    expect(screen.getByText('A')).toBeInTheDocument()
    expect(screen.getByText('B')).toBeInTheDocument()
    expect(screen.queryByText('C')).not.toBeInTheDocument()
    expect(screen.getByText('+2')).toBeInTheDocument()
  })
})
