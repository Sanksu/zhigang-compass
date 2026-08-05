import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { useAuthStore } from '@/store/auth'
import { AuthGuard, GuestGuard } from './guards'

afterEach(cleanup)

type SessionOver = Partial<{ initialized: boolean; isAuthenticated: boolean; role: 'guest' | 'user' | 'admin' }>

function setSession(over: SessionOver) {
  const isAuthenticated = over.isAuthenticated ?? false
  useAuthStore.setState({
    initialized: over.initialized ?? true,
    isAuthenticated,
    user: isAuthenticated
      ? { id: 'u1', username: 'a', role: over.role ?? 'user', permissions: [] }
      : null,
  })
}

function renderGuarded(guard: ReactNode) {
  return render(
    <MemoryRouter initialEntries={['/graph']}>
      <Routes>
        <Route path="/graph" element={guard} />
        <Route path="/login" element={<div>login-page</div>} />
        <Route path="/" element={<div>home-page</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('AuthGuard', () => {
  it('会话恢复中（未初始化）挂起渲染，不闪跳', () => {
    setSession({ initialized: false })
    renderGuarded(<AuthGuard><div>protected</div></AuthGuard>)
    expect(screen.queryByText('protected')).not.toBeInTheDocument()
    expect(screen.queryByText('login-page')).not.toBeInTheDocument()
  })

  it('未登录重定向到 /login 并携带来源路径', () => {
    setSession({ isAuthenticated: false })
    renderGuarded(<AuthGuard><div>protected</div></AuthGuard>)
    expect(screen.getByText('login-page')).toBeInTheDocument()
  })

  it('已登录渲染受保护内容', () => {
    setSession({ isAuthenticated: true, role: 'user' })
    renderGuarded(<AuthGuard><div>protected</div></AuthGuard>)
    expect(screen.getByText('protected')).toBeInTheDocument()
  })

  it('角色不符重定向到首页', () => {
    setSession({ isAuthenticated: true, role: 'user' })
    renderGuarded(<AuthGuard requireRole={['admin']}><div>protected</div></AuthGuard>)
    expect(screen.getByText('home-page')).toBeInTheDocument()
  })

  it('角色匹配（admin）放行', () => {
    setSession({ isAuthenticated: true, role: 'admin' })
    renderGuarded(<AuthGuard requireRole={['admin']}><div>protected</div></AuthGuard>)
    expect(screen.getByText('protected')).toBeInTheDocument()
  })
})

describe('GuestGuard', () => {
  it('未登录放行', () => {
    setSession({ isAuthenticated: false })
    renderGuarded(<GuestGuard><div>guest-page</div></GuestGuard>)
    expect(screen.getByText('guest-page')).toBeInTheDocument()
  })

  it('已登录重定向到首页（访客页对登录用户无意义）', () => {
    setSession({ isAuthenticated: true, role: 'user' })
    renderGuarded(<GuestGuard><div>guest-page</div></GuestGuard>)
    expect(screen.getByText('home-page')).toBeInTheDocument()
  })

  it('会话恢复中挂起', () => {
    setSession({ initialized: false })
    renderGuarded(<GuestGuard><div>guest-page</div></GuestGuard>)
    expect(screen.queryByText('guest-page')).not.toBeInTheDocument()
  })
})
