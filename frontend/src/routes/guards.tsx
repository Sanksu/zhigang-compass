import { Navigate, useLocation } from 'react-router'
import type { ReactNode } from 'react'
import { useAuthStore } from '@/store/auth'
import type { Role } from '@/lib/constants'

/**
 * 路由守卫 — 设计文档 §12.3 RBAC
 *
 * - guest 路由：已登录用户访问会重定向到 /
 * - auth 路由：未登录用户重定向到 /login
 * - admin 路由：非 admin 角色重定向到 /（无权限提示）
 */
interface GuardProps {
  children: ReactNode
  requireRole?: Role[]
}

export function AuthGuard({ children, requireRole }: GuardProps) {
  const location = useLocation()
  const { isAuthenticated, user } = useAuthStore()

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />
  }

  if (requireRole && user && !requireRole.includes(user.role)) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}

export function GuestGuard({ children }: GuardProps) {
  const { isAuthenticated } = useAuthStore()
  if (isAuthenticated) {
    return <Navigate to="/" replace />
  }
  return <>{children}</>
}
