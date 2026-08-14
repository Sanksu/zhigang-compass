import { useState, type FormEvent } from 'react'
import { useNavigate, useLocation } from 'react-router'
import { CompassMark } from '@/components/layout/compass-mark'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuthStore, permissionsOf } from '@/store/auth'
import { apiGet, apiPost, ApiError, setAccessToken, setRefreshToken } from '@/lib/api'
import type { components } from '@/types/api'

type LoginResult = components['schemas']['LoginResult']
type MeResult = components['schemas']['User']

/**
 * 登录页 — 设计文档 §10.2 /login
 * JWT 双 Token 内存存留（refresh_token 同时写 httpOnly Cookie），login 后调 /auth/me 获取用户态
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
      setAccessToken(data.access_token)
      setRefreshToken(data.refresh_token)
      // 拉取当前用户态（/auth/me 需 Bearer access_token）
      const me = await apiGet<MeResult>('/auth/me')
      setUser({ id: me.id, username: me.username, role: me.role, permissions: permissionsOf(me.role) })
      navigate(from, { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '登录失败，请检查用户名与密码')
    } finally {
      setLoading(false)
    }
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
          访客可直接浏览 <a href="/graph" className="underline hover:text-ink">能力图谱</a> · 还没有账户？{' '}
          <a href="/register" className="underline hover:text-ink">立即注册</a>
        </p>
      </div>
    </div>
  )
}
