import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SkillChip } from './skill-chips'

afterEach(cleanup)

describe('SkillChip', () => {
  it.each([
    ['must', 'border-primary/30'],
    ['nice', 'bg-subtle'],
    ['soft', '#ec4899'],
  ] as const)('%s 色调应用对应类', (tone, cls) => {
    render(<SkillChip tone={tone}>{tone}</SkillChip>)
    expect(screen.getByText(tone).className).toContain(cls)
  })

  it('可点击时触发 onClick 且带 hover 态', async () => {
    const fn = vi.fn()
    render(<SkillChip tone="must" onClick={fn}>必备</SkillChip>)
    await userEvent.click(screen.getByText('必备'))
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('透传 title 与自定义 className', () => {
    render(<SkillChip tone="nice" title="hint" className="custom-x">加分</SkillChip>)
    expect(screen.getByText('加分').title).toBe('hint')
    expect(screen.getByText('加分').className).toContain('custom-x')
  })
})