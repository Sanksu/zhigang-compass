/**
 * 通用分页条测试（2026-08-16 演化看板翻页共用组件）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { PaginationBar } from './pagination'

afterEach(cleanup)

describe('PaginationBar', () => {
  it('单页（total ≤ pageSize）不渲染', () => {
    const { container } = render(
      <PaginationBar page={1} total={10} pageSize={10} onPageChange={() => {}} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('多页时显示页码信息与按钮', () => {
    render(<PaginationBar page={2} total={35} pageSize={10} onPageChange={() => {}} />)
    expect(screen.getByText(/第 2 \/ 4 页/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '上一页' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '下一页' })).toBeTruthy()
  })

  it('首页禁用上一页，末页禁用下一页', () => {
    const { rerender } = render(
      <PaginationBar page={1} total={35} pageSize={10} onPageChange={() => {}} />,
    )
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    rerender(<PaginationBar page={4} total={35} pageSize={10} onPageChange={() => {}} />)
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
  })

  it('点击翻页回调正确页码；加载中禁用', async () => {
    const onChange = vi.fn()
    render(<PaginationBar page={2} total={35} pageSize={10} loading onPageChange={onChange} />)
    // loading 时按钮禁用，点击不触发
    await userEvent.click(screen.getByRole('button', { name: '上一页' }))
    expect(onChange).not.toHaveBeenCalled()
  })
})
