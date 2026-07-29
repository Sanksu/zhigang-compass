import { create } from 'zustand'
import type { Role } from '@/lib/constants'

/**
 * 认证状态 — 设计文档 §12.3
 *
 * Token 实际存储策略：
 * - access_token: httpOnly Cookie（后端 Set-Cookie），前端不可读
 * - refresh_token: 内存变量（lib/api.ts 中 setRefreshToken）
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

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  isAuthenticated: false,
  setUser: (user) => set({ user, isAuthenticated: !!user }),
  logout: () => set({ user: null, isAuthenticated: false }),
  hasPermission: (perm) => {
    const { user } = get()
    if (!user) return false
    if (user.role === 'admin') return true
    return user.permissions.includes(perm)
  },
}))
