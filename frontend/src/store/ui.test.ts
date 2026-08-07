import { beforeEach, describe, expect, it } from 'vitest'
import { useUIStore } from './ui'

beforeEach(() => {
  localStorage.clear()
  document.documentElement.classList.remove('dark')
  document.body.style.overflow = ''
})

describe('UI store：侧边栏', () => {
  it('初始侧边栏关闭', () => {
    expect(useUIStore.getState().sidebarOpen).toBe(false)
  })

  it('toggleSidebar 切换开合并锁 body 滚动', () => {
    useUIStore.getState().toggleSidebar()
    expect(useUIStore.getState().sidebarOpen).toBe(true)
    expect(document.body.style.overflow).toBe('hidden')
    useUIStore.getState().toggleSidebar()
    expect(useUIStore.getState().sidebarOpen).toBe(false)
    expect(document.body.style.overflow).toBe('')
  })

  it('closeSidebar 关闭并解锁滚动', () => {
    useUIStore.getState().toggleSidebar()
    useUIStore.getState().closeSidebar()
    expect(useUIStore.getState().sidebarOpen).toBe(false)
    expect(document.body.style.overflow).toBe('')
  })
})

describe('UI store：主题', () => {
  it('setTheme 应用 dark class 并持久化到 localStorage', () => {
    useUIStore.getState().setTheme('dark')
    expect(useUIStore.getState().theme).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(localStorage.getItem('zhigang-theme')).toBe('dark')
  })

  it('toggleTheme 在 light/dark 间切换', () => {
    useUIStore.getState().setTheme('light')
    useUIStore.getState().toggleTheme()
    expect(useUIStore.getState().theme).toBe('dark')
    expect(localStorage.getItem('zhigang-theme')).toBe('dark')
    useUIStore.getState().toggleTheme()
    expect(useUIStore.getState().theme).toBe('light')
    expect(localStorage.getItem('zhigang-theme')).toBe('light')
  })
})
