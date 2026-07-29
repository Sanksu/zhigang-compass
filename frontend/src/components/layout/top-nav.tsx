import { Link, useNavigate } from 'react-router'
import { CompassMark } from './compass-mark'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/store/auth'
import { useUIStore } from '@/store/ui'
import { ROLES } from '@/lib/constants'

/**
 * 顶部导航栏 — 固定高度 56px，底部 1px 细线
 * 左侧：汉堡菜单按钮（移动端）+ 罗盘标记 + 品牌名
 * 右侧：主题切换 + 用户信息 / 登录按钮
 */
export function TopNav() {
  const navigate = useNavigate()
  const { user, isAuthenticated, logout } = useAuthStore()
  const { toggleSidebar, theme, toggleTheme } = useUIStore()

  function handleLogout() {
    logout()
    navigate('/login')
  }

  return (
    <header className="sticky top-0 z-40 h-14 border-b border-border bg-canvas/80 backdrop-blur-md">
      <div className="flex h-full items-center justify-between px-4 lg:px-6">
        <div className="flex items-center gap-2">
          {/* 移动端/平板 — 汉堡菜单按钮 */}
          <button
            onClick={toggleSidebar}
            className="lg:hidden size-8 flex items-center justify-center rounded-md text-ink-muted hover:bg-elevated hover:text-ink transition-colors"
            aria-label="打开导航菜单"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </svg>
          </button>

          <Link to="/" className="flex items-center gap-2 group">
            <CompassMark size="sm" className="group-hover:rotate-90 transition-transform duration-500" />
            <span className="font-semibold tracking-tight text-ink">智岗罗盘</span>
            <span className="hidden sm:inline text-xs text-ink-faint font-mono">
              Zhigang Compass
            </span>
          </Link>
        </div>

        <div className="flex items-center gap-2">
          {/* 深/浅色模式切换 */}
          <button
            onClick={toggleTheme}
            className="size-8 flex items-center justify-center rounded-md text-ink-muted hover:bg-elevated hover:text-ink transition-colors"
            aria-label={theme === 'light' ? '切换深色模式' : '切换浅色模式'}
          >
            {theme === 'light' ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="5" />
                <line x1="12" y1="1" x2="12" y2="3" />
                <line x1="12" y1="21" x2="12" y2="23" />
                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                <line x1="1" y1="12" x2="3" y2="12" />
                <line x1="21" y1="12" x2="23" y2="12" />
                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
              </svg>
            )}
          </button>

          {isAuthenticated && user ? (
            <>
              <span className="text-sm text-ink-secondary hidden sm:inline">{user.username}</span>
              <span className="text-xs text-ink-faint hidden sm:inline">·</span>
              <span className="text-xs text-ink-muted hidden sm:inline">{ROLES[user.role]}</span>
              <Button variant="ghost" size="sm" onClick={handleLogout}>
                登出
              </Button>
            </>
          ) : (
            <Button variant="outline" size="sm" onClick={() => navigate('/login')}>
              登录
            </Button>
          )}
        </div>
      </div>
    </header>
  )
}