import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { useAuthStore } from '@/store/auth'
import { useUIStore } from '@/store/ui'
import { TopNav } from './top-nav'

vi.mock('@/lib/api', () => ({
  apiPost: vi.fn(),
  getRefreshToken: vi.fn(),
  setAccessToken: vi.fn(),
  setRefreshToken: vi.fn(),
  restoreSession: vi.fn(),
  registerAuthFailedHandler: vi.fn(),
}))

afterEach(cleanup)

function renderTopNav() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <TopNav />
    </MemoryRouter>,
  )
}

describe('TopNav', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, isAuthenticated: false, initialized: true })
    useUIStore.setState({ theme: 'light', sidebarOpen: false })
  })

  it('未登录显示登录按钮', () => {
    renderTopNav()
    expect(screen.getByRole('button', { name: '登录' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '登出' })).not.toBeInTheDocument()
  })

  it('已登录显示用户名与角色', () => {
    useAuthStore.setState({
      initialized: true,
      isAuthenticated: true,
      user: { id: 'u1', username: 'zhang', role: 'admin', permissions: ['*'] },
    })
    renderTopNav()
    expect(screen.getByText('zhang')).toBeInTheDocument()
    expect(screen.getByText('管理员')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '登出' })).toBeInTheDocument()
  })

  it('点击登出清空登录态', async () => {
    const user = userEvent.setup()
    useAuthStore.setState({
      initialized: true,
      isAuthenticated: true,
      user: { id: 'u1', username: 'zhang', role: 'user', permissions: [] },
    })
    renderTopNav()
    await user.click(screen.getByRole('button', { name: '登出' }))
    expect(useAuthStore.getState().isAuthenticated).toBe(false)
  })

  it('主题切换按钮 aria-label 随主题变化', async () => {
    const user = userEvent.setup()
    renderTopNav()
    // 浅色 → 提示切换深色
    expect(screen.getByRole('button', { name: '切换深色模式' })).toBeInTheDocument()
    // 真实点击触发 toggleTheme（userEvent 自带 act 包装，store 更新后同步重渲染）
    await user.click(screen.getByRole('button', { name: '切换深色模式' }))
    expect(screen.getByRole('button', { name: '切换浅色模式' })).toBeInTheDocument()
  })
})
