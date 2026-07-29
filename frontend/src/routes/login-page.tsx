import { useState, type FormEvent } from 'react'
import { useNavigate, useLocation } from 'react-router'
import { CompassMark } from '@/components/layout/compass-mark'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuthStore } from '@/store/auth'
import { setRefreshToken } from '@/lib/api'

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
      // TODO: 接入后端 POST /api/v1/auth/login
      // 临时演示：模拟登录
      const mockUser = {
        id: '00000000-0000-0000-0000-000000000001',
        username,
        role: 'admin' as const,
        permissions: [],
      }
      setRefreshToken('mock_refresh_token')
      setUser(mockUser)
      navigate(from, { replace: true })
    } catch {
      setError('登录失败，请检查用户名与密码')
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
                <label htmlFor="username" className="text-sm font-medium text-ink-secondary">
                  用户名
                </label>
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
                <label htmlFor="password" className="text-sm font-medium text-ink-secondary">
                  密码
                </label>
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
      </div>
    </div>
  )
}
