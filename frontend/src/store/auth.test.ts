import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiPost, getRefreshToken, setAccessToken, setRefreshToken } from '@/lib/api'
import { useAuthStore } from './auth'

vi.mock('@/lib/api', () => ({
  apiPost: vi.fn(),
  getRefreshToken: vi.fn(),
  setAccessToken: vi.fn(),
  setRefreshToken: vi.fn(),
  registerAuthFailedHandler: vi.fn(),
}))

const mockApiPost = vi.mocked(apiPost)
const mockGetRefreshToken = vi.mocked(getRefreshToken)

describe('auth store', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAuthStore.setState({ user: null, isAuthenticated: false })
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

  it('logout 携带 refresh_token 调用 /auth/logout 并清空 token', async () => {
    mockGetRefreshToken.mockReturnValue('refresh-abc')
    await useAuthStore.getState().logout()
    expect(mockApiPost).toHaveBeenCalledWith('/auth/logout', { refresh_token: 'refresh-abc' })
    expect(setAccessToken).toHaveBeenCalledWith(null)
    expect(setRefreshToken).toHaveBeenCalledWith(null)
    expect(useAuthStore.getState().isAuthenticated).toBe(false)
  })

  it('logout 无 refresh_token 时跳过服务端调用', async () => {
    mockGetRefreshToken.mockReturnValue(null)
    await useAuthStore.getState().logout()
    expect(mockApiPost).not.toHaveBeenCalled()
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
