import { useState } from 'react'
import { NavLink } from 'react-router'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/store/auth'
import { useUIStore } from '@/store/ui'
import { mainNav, adminNavGroups, type NavItem, type AdminNavGroup } from './nav-config'

/**
 * 侧边栏 — 桌面端 240px 固定，移动端作为抽屉式侧栏由菜单按钮控制展开收起
 * 08-16：管理后台改层级分组（管理 + 配置中心），组可折叠
 */
const FooterBlock = (
  <div className="border-t border-border px-3 py-3">
    <p className="text-xs text-ink-faint leading-relaxed">
      智岗罗盘 v0.1.0
      <br />
      <span className="font-mono">XH-202621</span>
    </p>
  </div>
)

export function Sidebar() {
  const { user } = useAuthStore()
  const { sidebarOpen, closeSidebar } = useUIStore()
  const role = user?.role
  // 管理组默认展开（本地状态，不持久化）
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())

  function filterByRole(items: NavItem[]): NavItem[] {
    if (!role) return items.filter((i) => !i.requireRole)
    return items.filter((i) => !i.requireRole || i.requireRole.includes(role))
  }

  const visibleMain = filterByRole(mainNav)
  const visibleGroups: AdminNavGroup[] = role === 'admin' ? adminNavGroups : []

  function toggleGroup(label: string) {
    setCollapsedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(label)) next.delete(label)
      else next.add(label)
      return next
    })
  }

  const navContent = (
    <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-1">
      {visibleMain.map((item) => (
        <NavItemLink key={item.to} item={item} onClick={closeSidebar} />
      ))}

      {visibleGroups.map((group) => {
        const items = filterByRole(group.items)
        if (items.length === 0) return null
        const collapsed = collapsedGroups.has(group.label)
        return (
          <div key={group.label}>
            <button
              type="button"
              onClick={() => toggleGroup(group.label)}
              className="flex w-full items-center justify-between rounded-md px-3 py-2 text-xs font-medium text-ink-faint uppercase tracking-wider hover:text-ink"
            >
              <span>{group.label}</span>
              <ChevronDown
                className={cn('size-3.5 transition-transform', collapsed && '-rotate-90')}
              />
            </button>
            {!collapsed && (
              <div className="space-y-0.5">
                {items.map((item) => (
                  <NavItemLink key={item.to} item={item} onClick={closeSidebar} />
                ))}
              </div>
            )}
          </div>
        )
      })}
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
{FooterBlock}
      </aside>

      {/* 桌面端固定侧栏 */}
      <aside className="hidden lg:flex w-60 flex-col border-r border-border bg-subtle">
        {navContent}
{FooterBlock}
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
