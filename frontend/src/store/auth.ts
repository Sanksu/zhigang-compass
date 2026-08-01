import { create } from 'zustand'
import type { Role } from '@/lib/constants'
import {
  apiPost,
  getRefreshToken,
  registerAuthFailedHandler,
  setAccessToken,
  setRefreshToken,
} from '@/lib/api'

/**
 * 认证状态 — 设计文档 §12.3
 *
 * Token 存储策略（与后端契约一致，Bearer 双 Token）：
 * - access_token / refresh_token：均为前端内存变量（lib/api.ts），
 *   请求拦截器附加 Authorization: Bearer
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
  setUser: (user: User | null) => void
  logout: () => void
  hasPermission: (perm: string) => boolean
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
    setUser: (user) => set({ user, isAuthenticated: !!user }),
    logout: async () => {
      // 通知服务端将 refresh_token 加入黑名单（Redis），失败不阻塞本地登出
      const refresh = getRefreshToken()
      if (refresh) {
        try {
          await apiPost('/auth/logout', { refresh_token: refresh })
        } catch {
          /* 忽略网络失败，本地登出仍执行 */
        }
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
