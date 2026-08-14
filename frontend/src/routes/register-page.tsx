import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { CompassMark } from '@/components/layout/compass-mark'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { apiPost, ApiError } from '@/lib/api'
import type { components } from '@/types/api'

/** 注册返回（契约 User 必填子集：id/username/role） */
type RegisterResult = Pick<components['schemas']['User'], 'id' | 'username' | 'role'>

/**
 * 注册页 — 与登录页同款布局（设计文档 §10.2）
 * 调用 /auth/register（默认 guest 角色），注册成功后跳转登录页
 */
export function RegisterPage() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (password.length < 6) {
      setError('密码至少 6 位')
      return
    }
    if (password !== confirmPassword) {
      setError('两次输入的密码不一致')
      return
    }
    setLoading(true)
    try {
      await apiPost<RegisterResult>('/auth/register', {
        username: username.trim(),
        password,
      })
      navigate('/login', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '注册失败，请稍后重试')
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
            <CardTitle>注册</CardTitle>
            <CardDescription>创建新账户（默认访客权限）</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="username">用户名</Label>
                <Input
                  id="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="请输入用户名（至少 3 字符）"
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
                  placeholder="请输入密码（至少 6 位）"
                  required
                  autoComplete="new-password"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="confirmPassword">确认密码</Label>
                <Input
                  id="confirmPassword"
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="请再次输入密码"
                  required
                  autoComplete="new-password"
                />
              </div>
              {error && (
                <p className="text-sm text-state-archived" role="alert">
                  {error}
                </p>
              )}
              <Button type="submit" className="w-full" disabled={loading}>
                {loading ? '注册中…' : '注册'}
              </Button>
            </form>
          </CardContent>
        </Card>

        <p className="text-center text-xs text-ink-faint">
          已有账户？<a href="/login" className="underline hover:text-ink">返回登录</a>
        </p>
      </div>
    </div>
  )
}
