import { useState } from 'react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from '@/components/ui/table'

/* ── Mock 数据 ── */

type UserRole = 'admin' | 'user' | 'guest'

interface CreatedUser {
  id: string
  username: string
  role: UserRole
  createdAt: string
  status: 'active' | 'inactive'
}

const mockRecentUsers: CreatedUser[] = [
  { id: '1', username: '赵岩', role: 'user', createdAt: '2026-07-29 10:30', status: 'active' },
  { id: '2', username: '钱枫', role: 'user', createdAt: '2026-07-28 16:20', status: 'active' },
  { id: '3', username: '孙丽', role: 'guest', createdAt: '2026-07-25 09:15', status: 'inactive' },
  { id: '4', username: '李华', role: 'user', createdAt: '2026-07-20 14:00', status: 'active' },
  { id: '5', username: '周敏', role: 'admin', createdAt: '2026-07-18 11:45', status: 'active' },
]

/* ── 页面 ── */

export function RegisterPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [role, setRole] = useState<UserRole>('user')
  const [creating, setCreating] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [success, setSuccess] = useState<string | null>(null)
  const [recentUsers, setRecentUsers] = useState(mockRecentUsers)

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

    setCreating(true)
    await new Promise((r) => setTimeout(r, 1500))
    setCreating(false)

    const newUser: CreatedUser = {
      id: crypto.randomUUID(),
      username: username.trim(),
      role,
      createdAt: new Date().toLocaleString('zh-CN', { hour12: false }),
      status: 'active',
    }

    setRecentUsers((prev) => [newUser, ...prev])
    setSuccess(`用户 ${newUser.username} 创建成功（角色: ${role}）`)
    setUsername('')
    setPassword('')
    setConfirmPassword('')
    setRole('user')
    setErrors({})
  }

  const roleBadge: Record<UserRole, { label: string }> = {
    admin: { label: '管理员' },
    user: { label: '用户' },
    guest: { label: '访客' },
  }

  const statusBadge: Record<CreatedUser['status'], { label: string; variant: 'candidate' | 'archived' }> = {
    active: { label: '活跃', variant: 'candidate' },
    inactive: { label: '停用', variant: 'archived' },
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="注册"
        description="管理员可创建新用户账户，创建的用户将收到初始密码通知"
      />

      {/* 创建用户表单 */}
      <Card>
        <CardHeader>
          <CardTitle>创建新用户</CardTitle>
          <CardDescription>填写用户信息后点击创建</CardDescription>
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

          <div className="grid gap-3">
            <Label>角色</Label>
            <Select value={role} onValueChange={(v: UserRole) => setRole(v)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="admin">admin</SelectItem>
                <SelectItem value="user">user</SelectItem>
                <SelectItem value="guest">guest</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {success && (
            <div className="rounded-md border border-state-candidate/20 bg-state-candidate/5 px-4 py-3 text-sm text-state-candidate">
              {success}
            </div>
          )}

          <Button onClick={handleCreate} disabled={creating}>
            {creating ? '创建中…' : '创建用户'}
          </Button>
        </CardContent>
      </Card>

      {/* 最近创建的用户列表 */}
      <Card>
        <CardHeader>
          <CardTitle>最近创建的用户</CardTitle>
          <CardDescription>所有已创建的用户账户列表</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>用户名</TableHead>
                <TableHead>角色</TableHead>
                <TableHead>创建时间</TableHead>
                <TableHead>状态</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recentUsers.map((u) => (
                <TableRow key={u.id}>
                  <TableCell className="font-medium text-ink">{u.username}</TableCell>
                  <TableCell>
                    <Badge variant={u.role === 'admin' ? 'default' : 'outline'}>
                      {roleBadge[u.role].label}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-ink-secondary">{u.createdAt}</TableCell>
                  <TableCell>
                    <Badge variant={statusBadge[u.status].variant}>{statusBadge[u.status].label}</Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
