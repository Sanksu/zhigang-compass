import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { ErrorFallback } from './error-fallback'

afterEach(cleanup)

/** 渲染在 /admin 路由下的兜底组件 + 可导航目标 /（以文本标记验证跳转结果） */
function renderAt(error: unknown) {
  return render(
    <MemoryRouter initialEntries={['/admin']}>
      <Routes>
        <Route path="/" element={<p>首页已到达</p>} />
        <Route path="/admin" element={<ErrorFallback error={error} />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ErrorFallback', () => {
  it('渲染中文文案与「重试」「返回首页」按钮', () => {
    renderAt(new Error('boom'))
    expect(screen.getByText('页面出现了一点问题')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '返回首页' })).toBeInTheDocument()
  })

  it('Error 实例展示 message 摘要', () => {
    renderAt(new Error('渲染崩溃'))
    expect(screen.getByText('渲染崩溃')).toBeInTheDocument()
  })

  it('非 Error 值 String 化兜底展示', () => {
    renderAt(500)
    expect(screen.getByText('500')).toBeInTheDocument()
  })

  it('「返回首页」点击后导航至 /', async () => {
    renderAt(new Error('x'))
    await userEvent.click(screen.getByRole('button', { name: '返回首页' }))
    expect(screen.getByText('首页已到达')).toBeInTheDocument()
  })
})
