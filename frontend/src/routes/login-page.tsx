import { useState, type FormEvent } from 'react'
import { useNavigate, useLocation } from 'react-router'
import { CompassMark } from '@/components/layout/compass-mark'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuthStore } from '@/store/auth'
import type { User } from '@/store/auth'
import { apiPost, ApiError, setRefreshToken } from '@/lib/api'

interface LoginResult {
  refresh_token: string
  user: {
    id: string
    username: string
    role: 'guest' | 'user' | 'admin'
    permissions: string[]
  }
}

/**
 * 登录页 — 设计文档 §10.2 /login
 * JWT 双 Token：access_token 由后端 Set-Cookie 写入 httpOnly Cookie
 */
export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const setUser = useAuthStore((s) => s.setUser)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const from = (location.state as { from?: string })?.from ?? '/'

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const data = await apiPost<LoginResult>('/auth/login', { username, password })
      setRefreshToken(data.refresh_token)
      setUser(data.user)
      navigate(from, { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '登录失败，请检查用户名与密码')
    } finally {
      setLoading(false)
    }
  }

  // Dev 专属快捷登录 — 后端未就绪时注入 mock 用户态以访问受保护页面
  // import.meta.env.DEV 在生产构建中被静态替换为 false，整个块被 tree-shake 移除
  function loginAsMock(role: User['role']) {
    const mockUsers: Record<User['role'], User> = {
      admin: { id: 'dev-admin', username: 'dev_admin', role: 'admin', permissions: ['*'] },
      user: { id: 'dev-user', username: 'dev_user', role: 'user', permissions: [] },
      guest: { id: 'dev-guest', username: 'dev_guest', role: 'guest', permissions: [] },
    }
    setUser(mockUsers[role])
    navigate(from, { replace: true })
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-subtle px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex flex-col items-center gap-3">
          <CompassMark size="lg" className="text-ink" />
          <div className="text-center">
            <h1 className="text-xl font-semibold tracking-tight">智岗罗盘</h1>
            <p className="text-sm text-ink-muted">多源异构驱动的岗位能力动态演化系统</p>
          </div>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>登录</CardTitle>
            <CardDescription>输入账户信息以继续</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="username">用户名</Label>
                <Input
                  id="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="请输入用户名"
                  required
                  autoComplete="username"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">密码</Label>
                <Input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="请输入密码"
                  required
                  autoComplete="current-password"
                />
              </div>
              {error && (
                <p className="text-sm text-state-archived" role="alert">
                  {error}
                </p>
              )}
              <Button type="submit" className="w-full" disabled={loading}>
                {loading ? '登录中…' : '登录'}
              </Button>
            </form>
          </CardContent>
        </Card>

        <p className="text-center text-xs text-ink-faint">
          访客可直接浏览 <a href="/graph" className="underline hover:text-ink">能力图谱</a>
        </p>

        {/* Dev 专属快捷登录 — 后端未就绪时使用，生产构建被 tree-shake 移除 */}
        {import.meta.env.DEV && (
          <div className="rounded-md border border-dashed border-state-emerging/40 bg-state-emerging/5 p-3 space-y-2">
            <p className="text-xs font-medium text-state-emerging">Dev 快捷登录（mock）</p>
            <p className="text-[10px] text-ink-muted">后端未就绪时跳过真实认证，直接注入 mock 用户态</p>
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                variant="default"
                className="flex-1 h-7 text-xs"
                onClick={() => loginAsMock('admin')}
              >
                admin
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="flex-1 h-7 text-xs"
                onClick={() => loginAsMock('user')}
              >
                user
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="flex-1 h-7 text-xs"
                onClick={() => loginAsMock('guest')}
              >
                guest
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
