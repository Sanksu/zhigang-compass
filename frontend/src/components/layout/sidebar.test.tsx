import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { useAuthStore } from '@/store/auth'
import { useUIStore } from '@/store/ui'
import { Sidebar } from './sidebar'

afterEach(cleanup)

function setRole(role: 'guest' | 'user' | 'admin' | null) {
  useAuthStore.setState({
    initialized: true,
    isAuthenticated: role !== null,
    user: role === null ? null : { id: 'u1', username: 'a', role, permissions: [] },
  })
}

function renderSidebar() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Sidebar />
    </MemoryRouter>,
  )
}

describe('Sidebar 导航过滤（§12.3 RBAC）', () => {
  beforeEach(() => {
    useUIStore.setState({ sidebarOpen: false })
  })

  it('未登录只显示公开项，不显示管理后台', () => {
    setRole(null)
    renderSidebar()
    // 移动端抽屉 + 桌面端侧栏均渲染 navContent，存在性用 getAllByText 断言
    expect(screen.getAllByText('仪表盘').length).toBeGreaterThan(0)
    expect(screen.getAllByText('能力图谱').length).toBeGreaterThan(0)
    expect(screen.queryAllByText('简历匹配')).toHaveLength(0)
    expect(screen.queryAllByText('管理后台')).toHaveLength(0)
  })

  it('user 角色显示需登录项，管理后台仍隐藏', () => {
    setRole('user')
    renderSidebar()
    expect(screen.getAllByText('简历匹配').length).toBeGreaterThan(0)
    expect(screen.queryAllByText('管理后台')).toHaveLength(0)
  })

  it('admin 角色显示完整主导航 + 管理/配置中心两组', () => {
    setRole('admin')
    renderSidebar()
    expect(screen.getAllByText('简历匹配').length).toBeGreaterThan(0)
    expect(screen.getAllByText('管理').length).toBeGreaterThan(0)
    expect(screen.getAllByText('配置中心').length).toBeGreaterThan(0)
    expect(screen.getAllByText('岗位审核').length).toBeGreaterThan(0)
    expect(screen.getAllByText('采集与限频').length).toBeGreaterThan(0)
  })

  it('sidebarOpen 时显示移动端抽屉面板', () => {
    setRole('admin')
    useUIStore.setState({ sidebarOpen: true })
    renderSidebar()
    // 抽屉标题与关闭按钮存在
    expect(screen.getByText('导航菜单')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '关闭侧栏' })).toBeInTheDocument()
  })
})
