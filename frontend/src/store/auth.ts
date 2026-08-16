import { create } from 'zustand'
import type { Role } from '@/lib/constants'
import {
  apiPost,
  getRefreshToken,
  registerAuthFailedHandler,
  restoreSession,
  setAccessToken,
  setRefreshToken,
} from '@/lib/api'

/**
 * 认证状态 — 设计文档 §12.3
 *
 * Token 存储策略（与后端契约一致，Bearer 双 Token）：
 * - access_token / refresh_token：均为前端内存变量（lib/api.ts），
 *   请求拦截器附加 Authorization: Bearer
 * - refresh_token 同时由后端写入 httpOnly Cookie：
 *   刷新页面后内存清空，通过 restoreSession() 用 Cookie 静默恢复会话
 *
 * 此 store 仅维护用户信息与权限，不存储 token 本身
 */

export interface User {
  id: string
  username: string
  role: Role
  permissions: string[]
}

interface AuthState {
  user: User | null
  isAuthenticated: boolean
  /** 应用启动时是否已完成会话恢复（避免刷新时闪跳登录页） */
  initialized: boolean
  setUser: (user: User | null) => void
  initialize: () => Promise<void>
  logout: () => void
  hasPermission: (perm: string) => boolean
}

/** 后端 me 不返回权限列表，按角色映射（与后端 core/security.py ROLE_PERMISSIONS 一致） */
export function permissionsOf(role: Role): string[] {
  if (role === 'admin') return ['*']
  if (role === 'user') return ['graph:read', 'graph:write', 'data:read', 'match:run']
  return ['graph:read'] // guest
}

export const useAuthStore = create<AuthState>((set, get) => {
  // 401 续期失败时由 http 拦截器回调，清理本地状态并跳登录页
  registerAuthFailedHandler(() => {
    set({ user: null, isAuthenticated: false })
    if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
  })

  return {
    user: null,
    isAuthenticated: false,
    initialized: false,
    setUser: (user) => set({ user, isAuthenticated: !!user }),
    initialize: async () => {
      if (get().initialized) return
      try {
        const me = await restoreSession()
        if (me) {
          set({
            user: { id: me.id, username: me.username, role: me.role, permissions: permissionsOf(me.role) },
            isAuthenticated: true,
          })
        }
      } finally {
        set({ initialized: true })
      }
    },
    logout: async () => {
      // 通知服务端将 refresh_token 加入黑名单并清除 httpOnly Cookie；
      // 即使内存 token 已空（刷新后场景）也调用，让后端清 Cookie。失败不阻塞本地登出
      try {
        await apiPost('/auth/logout', { refresh_token: getRefreshToken() })
      } catch {
        /* 忽略网络失败，本地登出仍执行 */
      }
      setAccessToken(null)
      setRefreshToken(null)
      set({ user: null, isAuthenticated: false })
    },
    hasPermission: (perm) => {
      const { user } = get()
      if (!user) return false
      if (user.role === 'admin') return true
      return user.permissions.includes(perm)
    },
  }
})
