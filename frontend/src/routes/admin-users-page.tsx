import { useState } from 'react'
import { Shield, UserPlus, Users } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ROLES, type Role } from '@/lib/constants'

type UserStatus = 'active' | 'disabled'

interface UserRow {
  id: string
  username: string
  role: Role
  status: UserStatus
  lastLogin: string
  createdAt: string
}

/** 8 条 mock 用户 — 覆盖三种角色与两种状态 */
const MOCK_USERS: UserRow[] = [
  { id: 'u1', username: 'admin_zhang', role: 'admin', status: 'active', lastLogin: '07-29 14:32', createdAt: '2025-03-15' },
  { id: 'u2', username: 'admin_li', role: 'admin', status: 'active', lastLogin: '07-29 13:18', createdAt: '2025-03-20' },
  { id: 'u3', username: 'user_wang', role: 'user', status: 'active', lastLogin: '07-29 12:05', createdAt: '2025-04-10' },
  { id: 'u4', username: 'user_chen', role: 'user', status: 'disabled', lastLogin: '2025-06-01 02:00', createdAt: '2025-05-12' },
  { id: 'u5', username: 'user_liu', role: 'user', status: 'active', lastLogin: '07-29 10:47', createdAt: '2025-05-20' },
  { id: 'u6', username: 'guest_zhao', role: 'guest', status: 'active', lastLogin: '07-28 09:00', createdAt: '2025-07-28' },
  { id: 'u7', username: 'user_sun', role: 'user', status: 'active', lastLogin: '07-29 08:30', createdAt: '2025-07-25' },
  { id: 'u8', username: 'user_zhou', role: 'user', status: 'active', lastLogin: '07-29 07:15', createdAt: '2025-07-29' },
]

/** 角色 Badge variant — admin 墨色凸显权限，user 中性，guest 灰 */
const ROLE_VARIANT: Record<Role, 'default' | 'outline' | 'candidate'> = {
  admin: 'default',
  user: 'outline',
  guest: 'candidate',
}

const STATUS_META: Record<UserStatus, { variant: 'emerging' | 'archived'; label: string }> = {
  active: { variant: 'emerging', label: '活跃' },
  disabled: { variant: 'archived', label: '禁用' },
}

export function AdminUsersPage() {
  const [users, setUsers] = useState(MOCK_USERS)
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', role: 'user' as Role })

  function setRole(id: string, role: Role) {
    setUsers((u) => u.map((x) => (x.id === id ? { ...x, role } : x)))
  }

  function toggleStatus(id: string) {
    setUsers((u) =>
      u.map((x) => (x.id === id ? { ...x, status: x.status === 'active' ? 'disabled' : 'active' } : x)),
    )
  }

  function createUser() {
    if (!form.username.trim()) return
    const newUser: UserRow = {
      id: `u-${Date.now()}`,
      username: form.username.trim(),
      role: form.role,
      status: 'active',
      lastLogin: '—',
      createdAt: '刚刚',
    }
    setUsers((u) => [newUser, ...u])
    setForm({ username: '', password: '', role: 'user' })
    setCreateOpen(false)
  }

  return (
    <>
      <PageHeader
        title="用户管理"
        description="RBAC 权限分配 · 启用/禁用 · 账户生命周期"
        actions={
          <Button onClick={() => setCreateOpen(true)}>
            <UserPlus className="size-4" />
            创建用户
          </Button>
        }
      />

      {/* 统计卡 */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <Users className="size-4 text-ink-faint" />
              <span className="text-xs font-mono text-ink-faint">全量</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">248</div>
            <div className="text-xs text-ink-muted mt-1">总用户数</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <Shield className="size-4 text-ink-faint" />
              <span className="text-xs font-mono text-ink-faint">高权限</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">
              {users.filter((u) => u.role === 'admin').length}
            </div>
            <div className="text-xs text-ink-muted mt-1">admin 数</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <UserPlus className="size-4 text-ink-faint" />
              <span className="text-xs font-mono text-state-emerging">+5</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">5</div>
            <div className="text-xs text-ink-muted mt-1">今日新增</div>
          </CardContent>
        </Card>
      </div>

      {/* 用户列表 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center justify-between">
            <span>用户列表</span>
            <span className="text-xs font-normal text-ink-faint">{users.length} 条</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>用户名</TableHead>
                <TableHead>角色</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>最后登录</TableHead>
                <TableHead>创建时间</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((u) => {
                const roleMeta = ROLE_VARIANT[u.role]
                const statusMeta = STATUS_META[u.status]
                return (
                  <TableRow key={u.id}>
                    <TableCell className="font-medium font-mono">{u.username}</TableCell>
                    <TableCell>
                      <Badge variant={roleMeta}>{ROLES[u.role]}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={statusMeta.variant}>{statusMeta.label}</Badge>
                    </TableCell>
                    <TableCell className="text-xs font-mono text-ink-muted">{u.lastLogin}</TableCell>
                    <TableCell className="text-xs font-mono text-ink-muted">{u.createdAt}</TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-2">
                        {/* 行内角色切换 — Select 受控，立即生效 */}
                        <Select value={u.role} onValueChange={(v) => setRole(u.id, v as Role)}>
                          <SelectTrigger className="h-8 w-24">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {(Object.keys(ROLES) as Role[]).map((r) => (
                              <SelectItem key={r} value={r}>{ROLES[r]}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <Button
                          size="sm"
                          variant={u.status === 'active' ? 'outline' : 'default'}
                          onClick={() => toggleStatus(u.id)}
                        >
                          {u.status === 'active' ? '禁用' : '启用'}
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* 创建用户 Dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>创建用户</DialogTitle>
            <DialogDescription>新建账户默认为「活跃」状态，角色可后续调整</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>用户名</Label>
              <Input
                value={form.username}
                onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
                placeholder="user_xxx"
              />
            </div>
            <div className="space-y-1.5">
              <Label>密码</Label>
              <Input
                type="password"
                value={form.password}
                onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
                placeholder="••••••••"
              />
            </div>
            <div className="space-y-1.5">
              <Label>角色</Label>
              <Select value={form.role} onValueChange={(v) => setForm((f) => ({ ...f, role: v as Role }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(Object.keys(ROLES) as Role[]).map((r) => (
                    <SelectItem key={r} value={r}>{ROLES[r]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>取消</Button>
            <Button disabled={!form.username.trim()} onClick={createUser}>创建</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
