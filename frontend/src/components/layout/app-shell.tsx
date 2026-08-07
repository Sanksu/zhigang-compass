import { Outlet } from 'react-router'
import { TopNav } from './top-nav'
import { Sidebar } from './sidebar'

/**
 * 应用外壳 — 顶部导航 + 侧边栏 + 主内容区
 *
 * 布局策略（设计文档 §10.5）：
 * - 桌面端 ≥1025px：双栏全功能（侧边栏 + 内容）
 * - 平板 641-1024px：侧边栏隐藏，内容全宽
 * - 移动端 ≤640px：纯单栏
 */
export function AppShell() {
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
