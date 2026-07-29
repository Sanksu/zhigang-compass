import { NavLink } from 'react-router'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/store/auth'
import { useUIStore } from '@/store/ui'
import { mainNav, adminNav, type NavItem } from './nav-config'

/**
 * 侧边栏 — 桌面端 240px 固定，移动端作为抽屉式侧栏由菜单按钮控制展开收起
 */
export function Sidebar() {
  const { user } = useAuthStore()
  const { sidebarOpen, closeSidebar } = useUIStore()
  const role = user?.role

  function filterByRole(items: NavItem[]): NavItem[] {
    if (!role) return items.filter((i) => !i.requireRole)
    return items.filter((i) => !i.requireRole || i.requireRole.includes(role))
  }

  const visibleMain = filterByRole(mainNav)
  const visibleAdmin = role === 'admin' ? adminNav : []

  const navContent = (
    <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-1">
      {visibleMain.map((item) => (
        <NavItemLink key={item.to} item={item} onClick={closeSidebar} />
      ))}

      {visibleAdmin.length > 0 && (
        <>
          <div className="pt-4 pb-2 px-3">
            <span className="text-xs font-medium text-ink-faint uppercase tracking-wider">
              管理后台
            </span>
          </div>
          {visibleAdmin.map((item) => (
            <NavItemLink key={item.to} item={item} onClick={closeSidebar} />
          ))}
        </>
      )}
    </nav>
  )

  return (
    <>
      {/* 移动端抽屉 — 遮罩层 */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 lg:hidden"
          onClick={closeSidebar}
          aria-hidden="true"
        />
      )}

      {/* 移动端抽屉 — 侧栏面板 */}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-50 w-60 flex flex-col border-r border-border bg-subtle',
          'transition-transform duration-300 ease-in-out lg:hidden',
          sidebarOpen ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <div className="flex h-14 items-center justify-between px-4 border-b border-border">
          <span className="font-semibold tracking-tight text-ink">导航菜单</span>
          <button
            onClick={closeSidebar}
            className="size-8 flex items-center justify-center rounded-md text-ink-muted hover:bg-elevated hover:text-ink transition-colors"
            aria-label="关闭侧栏"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        {navContent}
        <div className="border-t border-border px-3 py-3">
          <p className="text-xs text-ink-faint leading-relaxed">
            智岗罗盘 v0.1.0
            <br />
            <span className="font-mono">XH-202621</span>
          </p>
        </div>
      </aside>

      {/* 桌面端固定侧栏 */}
      <aside className="hidden lg:flex w-60 flex-col border-r border-border bg-subtle">
        {navContent}
        <div className="border-t border-border px-3 py-3">
          <p className="text-xs text-ink-faint leading-relaxed">
            智岗罗盘 v0.1.0
            <br />
            <span className="font-mono">XH-202621</span>
          </p>
        </div>
      </aside>
    </>
  )
}

function NavItemLink({ item, onClick }: { item: NavItem; onClick?: () => void }) {
  const Icon = item.icon
  return (
    <NavLink
      to={item.to}
      end={item.to === '/'}
      onClick={onClick}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors',
          isActive
            ? 'bg-canvas text-ink font-medium shadow-sm'
            : 'text-ink-secondary hover:bg-canvas/60 hover:text-ink',
        )
      }
    >
      <Icon className="size-4 shrink-0" />
      <span>{item.label}</span>
    </NavLink>
  )
}