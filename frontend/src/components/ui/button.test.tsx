import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Button } from './button'

describe('Button', () => {
  it('渲染默认按钮并响应点击', async () => {
    const onClick = vi.fn()
    const user = userEvent.setup()
    render(<Button onClick={onClick}>点我</Button>)
    expect(screen.getByRole('button', { name: '点我' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '点我' }))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('disabled 状态不触发点击', async () => {
    const onClick = vi.fn()
    const user = userEvent.setup()
    render(<Button disabled onClick={onClick}>禁用</Button>)
    await user.click(screen.getByRole('button', { name: '禁用' }))
    expect(onClick).not.toHaveBeenCalled()
  })

  it('variant 应用对应类名', () => {
    render(<Button variant="destructive">删除</Button>)
    expect(screen.getByRole('button', { name: '删除' }).className).toContain('bg-state-archived')
  })

  it('asChild 渲染为子元素', () => {
    render(
      <Button asChild>
        <a href="/x">链接</a>
      </Button>,
    )
    expect(screen.getByRole('link', { name: '链接' })).toBeInTheDocument()
  })
})
