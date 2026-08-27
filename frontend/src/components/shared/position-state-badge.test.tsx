import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { POSITION_STATE_META, PositionStateBadge } from './position-state-badge'

afterEach(cleanup)

describe('POSITION_STATE_META', () => {
  it('覆盖契约状态机 + 流转展示态，label 与 variant 成对', () => {
    expect(Object.keys(POSITION_STATE_META)).toHaveLength(7)
    expect(POSITION_STATE_META.candidate).toMatchObject({ label: '候选', variant: 'candidate' })
    expect(POSITION_STATE_META.active).toMatchObject({ label: '活跃', variant: 'active' })
    expect(POSITION_STATE_META.rejected).toMatchObject({ label: '驳回', variant: 'archived' })
  })
})

describe('PositionStateBadge', () => {
  it('按状态渲染中文标签与对应 variant 类', () => {
    render(<PositionStateBadge state="declining" />)
    expect(screen.getByText('衰退').className).toContain('bg-state-declining/10')
  })

  it('未知状态回退为原文 + outline', () => {
    render(<PositionStateBadge state="mystery" />)
    const el = screen.getByText('mystery')
    expect(el.className).toContain('text-ink-secondary') // outline 变体
  })

  it('可覆盖 label 与 variant（流转记录展示原始 state 串）', () => {
    render(<PositionStateBadge state="emerging" label="emerging" variant="outline" />)
    expect(screen.getByText('emerging')).toBeInTheDocument()
  })

  it('透传自定义 className', () => {
    render(<PositionStateBadge state="stable" className="custom-x" />)
    expect(screen.getByText('稳定').className).toContain('custom-x')
  })
})