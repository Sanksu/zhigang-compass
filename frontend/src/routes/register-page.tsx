import { useState } from 'react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from '@/components/ui/table'
import { apiPost, ApiError } from '@/lib/api'

/* ── 页面 ── */

interface RegisteredUser {
  id: string
  username: string
  role: string
  createdAt: string
}

export function RegisterPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [creating, setCreating] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [success, setSuccess] = useState<string | null>(null)
  // 本会话注册成功的账户（初始为空，来自真实 /auth/register 返回）
  const [recentUsers, setRecentUsers] = useState<RegisteredUser[]>([])

  function validate(): boolean {
    const next: Record<string, string> = {}
    if (!username.trim()) next.username = '请输入用户名'
    if (!password) next.password = '请输入密码'
    else if (password.length < 6) next.password = '密码至少 6 位'
    if (!confirmPassword) next.confirmPassword = '请确认密码'
    else if (password !== confirmPassword) next.confirmPassword = '两次密码不一致'
    setErrors(next)
    return Object.keys(next).length === 0
  }

  async function handleCreate() {
    setSuccess(null)
    if (!validate()) return

    console.log('[register] 开始提交注册:', { username: username.trim() })
    setCreating(true)
    try {
      const res = await apiPost<{ id: string; username: string; role: string }>('/auth/register', {
        username: username.trim(),
        password,
      })
      console.log('[register] 注册成功:', { id: res.id, username: res.username, role: res.role })
      setRecentUsers((prev) => [
        {
          id: res.id,
          username: res.username,
          role: res.role,
          createdAt: new Date().toLocaleString('zh-CN', { hour12: false }),
        },
        ...prev,
      ])
      setSuccess(`账户 ${res.username} 注册成功`)
      setUsername('')
      setPassword('')
      setConfirmPassword('')
      setErrors({})
    } catch (e) {
      console.error('[register] 注册失败:', e)
      setErrors({ username: e instanceof ApiError ? e.message : '注册失败，请稍后重试' })
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader title="注册" description="创建新账户，注册成功后可直接登录" />

      {/* 注册表单 */}
      <Card>
        <CardHeader>
          <CardTitle>注册新账户</CardTitle>
          <CardDescription>填写账户信息后点击注册</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3">
            <Label htmlFor="reg-username">用户名 *</Label>
            <Input
              id="reg-username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="输入用户名"
            />
            {errors.username && <p className="text-sm text-state-archived">{errors.username}</p>}
          </div>

          <div className="grid gap-3">
            <Label htmlFor="reg-password">密码 *</Label>
            <Input
              id="reg-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="至少 6 位"
            />
            {errors.password && <p className="text-sm text-state-archived">{errors.password}</p>}
          </div>

          <div className="grid gap-3">
            <Label htmlFor="reg-confirm">确认密码 *</Label>
            <Input
              id="reg-confirm"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="再次输入密码"
            />
            {errors.confirmPassword && (
              <p className="text-sm text-state-archived">{errors.confirmPassword}</p>
            )}
          </div>

          {success && (
            <div className="rounded-md border border-state-candidate/20 bg-state-candidate/5 px-4 py-3 text-sm text-state-candidate">
              {success}
            </div>
          )}

          <Button onClick={handleCreate} disabled={creating}>
            {creating ? '注册中…' : '注册'}
          </Button>
        </CardContent>
      </Card>

      {/* 本会话注册的账户列表 */}
      <Card>
        <CardHeader>
          <CardTitle>最近注册的账户</CardTitle>
          <CardDescription>本次会话中注册成功的账户</CardDescription>
        </CardHeader>
        <CardContent>
          {recentUsers.length === 0 ? (
            <p className="py-8 text-center text-sm text-ink-faint">暂无注册记录</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>用户名</TableHead>
                  <TableHead>角色</TableHead>
                  <TableHead>注册时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {recentUsers.map((u) => (
                  <TableRow key={u.id}>
                    <TableCell className="font-medium text-ink">{u.username}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{u.role}</Badge>
                    </TableCell>
                    <TableCell className="text-ink-secondary">{u.createdAt}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
