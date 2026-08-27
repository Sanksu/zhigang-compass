import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RefreshButton } from './refresh-button'

afterEach(cleanup)

describe('RefreshButton', () => {
  it('默认文案「刷新」且非禁用', () => {
    render(<RefreshButton />)
    const btn = screen.getByText('刷新')
    expect(btn).toBeEnabled()
  })

  it('loading 态禁用并显示「刷新中…」', () => {
    render(<RefreshButton loading />)
    const btn = screen.getByText('刷新中…')
    expect(btn).toBeDisabled()
  })

  it('自定义文案 + 触发 onClick', async () => {
    const fn = vi.fn()
    render(<RefreshButton onClick={fn}>重载</RefreshButton>)
    await userEvent.click(screen.getByText('重载'))
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('loading 时不触发 onClick（按钮禁用）', async () => {
    const fn = vi.fn()
    render(<RefreshButton loading onClick={fn}>刷新</RefreshButton>)
    const btn = screen.getByText('刷新').closest('button') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    await userEvent.click(btn)
    expect(fn).not.toHaveBeenCalled()
  })
})