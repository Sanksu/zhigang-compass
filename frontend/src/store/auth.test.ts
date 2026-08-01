import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiPost, getRefreshToken, restoreSession, setAccessToken, setRefreshToken } from '@/lib/api'
import { useAuthStore } from './auth'

vi.mock('@/lib/api', () => ({
  apiPost: vi.fn(),
  getRefreshToken: vi.fn(),
  setAccessToken: vi.fn(),
  setRefreshToken: vi.fn(),
  restoreSession: vi.fn(),
  registerAuthFailedHandler: vi.fn(),
}))

const mockApiPost = vi.mocked(apiPost)
const mockGetRefreshToken = vi.mocked(getRefreshToken)
const mockRestoreSession = vi.mocked(restoreSession)

describe('auth store', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAuthStore.setState({ user: null, isAuthenticated: false, initialized: false })
  })

  it('setUser 设置用户与登录态', () => {
    useAuthStore.getState().setUser({
      id: 'u1', username: 'zhang', role: 'user', permissions: ['graph:read'],
    })
    expect(useAuthStore.getState().isAuthenticated).toBe(true)
    expect(useAuthStore.getState().user?.role).toBe('user')
  })

  it('setUser(null) 清空本地状态', () => {
    useAuthStore.getState().setUser({ id: 'u1', username: 'a', role: 'user', permissions: [] })
    useAuthStore.getState().setUser(null)
    expect(useAuthStore.getState().isAuthenticated).toBe(false)
    expect(useAuthStore.getState().user).toBeNull()
  })

  it('initialize: Cookie 会话有效时恢复用户态并标记已初始化', async () => {
    mockRestoreSession.mockResolvedValue({ id: 'u1', username: 'zhang', role: 'admin' })
    await useAuthStore.getState().initialize()
    expect(useAuthStore.getState().initialized).toBe(true)
    expect(useAuthStore.getState().isAuthenticated).toBe(true)
    expect(useAuthStore.getState().user?.permissions).toEqual(['*'])
  })

  it('initialize: 无有效 Cookie 会话时保持未登录', async () => {
    mockRestoreSession.mockResolvedValue(null)
    await useAuthStore.getState().initialize()
    expect(useAuthStore.getState().initialized).toBe(true)
    expect(useAuthStore.getState().isAuthenticated).toBe(false)
  })

  it('initialize: 重复调用只执行一次', async () => {
    mockRestoreSession.mockResolvedValue({ id: 'u1', username: 'a', role: 'user' })
    await useAuthStore.getState().initialize()
    await useAuthStore.getState().initialize()
    expect(mockRestoreSession).toHaveBeenCalledTimes(1)
  })

  it('logout 携带 refresh_token 调用 /auth/logout 并清空 token', async () => {
    mockGetRefreshToken.mockReturnValue('refresh-abc')
    await useAuthStore.getState().logout()
    expect(mockApiPost).toHaveBeenCalledWith('/auth/logout', { refresh_token: 'refresh-abc' })
    expect(setAccessToken).toHaveBeenCalledWith(null)
    expect(setRefreshToken).toHaveBeenCalledWith(null)
    expect(useAuthStore.getState().isAuthenticated).toBe(false)
  })

  it('logout 无内存 refresh_token 时仍调用服务端（后端靠 httpOnly Cookie 清除）', async () => {
    mockGetRefreshToken.mockReturnValue(null)
    await useAuthStore.getState().logout()
    expect(mockApiPost).toHaveBeenCalledWith('/auth/logout', { refresh_token: null })
  })

  it('logout 服务端失败不阻塞本地登出', async () => {
    mockGetRefreshToken.mockReturnValue('refresh-abc')
    mockApiPost.mockRejectedValue(new Error('network'))
    await expect(useAuthStore.getState().logout()).resolves.toBeUndefined()
    expect(useAuthStore.getState().isAuthenticated).toBe(false)
  })

  it('hasPermission: admin 恒为 true', () => {
    useAuthStore.getState().setUser({ id: 'u1', username: 'a', role: 'admin', permissions: [] })
    expect(useAuthStore.getState().hasPermission('any:perm')).toBe(true)
  })

  it('hasPermission: 未登录一律 false', () => {
    expect(useAuthStore.getState().hasPermission('graph:read')).toBe(false)
  })
})
