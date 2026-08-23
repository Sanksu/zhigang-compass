import { useEffect } from 'react'
import { Outlet } from 'react-router'
import { TopNav } from './top-nav'
import { Sidebar } from './sidebar'
import { useUIStore } from '@/store/ui'

/**
 * 应用外壳 — 顶部导航 + 侧边栏 + 主内容区
 *
 * 布局策略（设计文档 §10.5）：
 * - 桌面端 ≥1025px：双栏全功能（侧边栏 + 内容）
 * - 平板 641-1024px：侧边栏隐藏，内容全宽
 * - 移动端 ≤640px：纯单栏
 * - 大屏演示模式（focusMode）：隐藏顶导与侧栏，内容占满视口（图谱页答辩用；
 *   Esc 退出——详情抽屉打开时由其自行消费 Esc 关抽屉，再按才退出演示）
 */
export function AppShell() {
  const focusMode = useUIStore((s) => s.focusMode)
  const closeFocusMode = useUIStore((s) => s.closeFocusMode)

  useEffect(() => {
    if (!focusMode) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      closeFocusMode()
      // 页内演示态退出时同步退出浏览器全屏（浏览器自身消费的 Esc 不达此处）
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [focusMode, closeFocusMode])

  if (focusMode) {
    return (
      <div className="flex h-screen flex-col bg-canvas">
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    )
  }

  return (
    <div className="flex h-screen flex-col bg-canvas">
      <TopNav />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-y-auto">
          <div className="px-4 py-6 lg:px-8 lg:py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
