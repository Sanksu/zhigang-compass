import { useEffect, useState } from 'react'
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
import { apiGet, apiPost, apiPut, ApiError } from '@/lib/api'
import { useAuthStore } from '@/store/auth'

type UserStatus = 'active' | 'disabled'

interface UserRow {
  id: string
  username: string
  role: Role
  status: UserStatus
  createdAt: string
}

/** 后端 /admin/users 返回项 */
interface BackendUser {
  id: string
  username: string
  role: Role
  is_active: boolean
  created_at: string | null
  updated_at: string | null
}

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

function toRow(u: BackendUser): UserRow {
  return {
    id: u.id,
    username: u.username,
    role: u.role,
    status: u.is_active ? 'active' : 'disabled',
    createdAt: u.created_at ? new Date(u.created_at).toLocaleDateString('zh-CN') : '—',
  }
}

export function AdminUsersPage() {
  const { user: currentUser } = useAuthStore()
  const [users, setUsers] = useState<UserRow[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', role: 'user' as Role })

  // 自保护：当前登录管理员不可修改自己的角色/禁用自己（M6，后端同样拦截）
  const isSelf = (id: string) => id === currentUser?.id

  async function load() {
    try {
      const res = await apiGet<{ items: BackendUser[]; total: number }>('/admin/users?page=1&size=100')
      setUsers(res.items.map(toRow))
      setTotal(res.total)
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '用户列表加载失败')
    } finally {
      setLoading(false)
    }
  }

  // 初始加载（setState 均在异步回调内）
  useEffect(() => {
    let cancelled = false
    apiGet<{ items: BackendUser[]; total: number }>('/admin/users?page=1&size=100')
      .then((res) => {
        if (cancelled) return
        setUsers(res.items.map(toRow))
        setTotal(res.total)
        setError(null)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? e.message : '用户列表加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function setRole(id: string, role: Role) {
    if (isSelf(id)) {
      setError('不能修改当前登录账户的角色')
      return
    }
    try {
      await apiPut(`/admin/users/${id}`, { role })
      await load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '角色更新失败')
    }
  }

  async function toggleStatus(id: string) {
    if (isSelf(id)) {
      setError('不能禁用当前登录账户')
      return
    }
    const u = users.find((x) => x.id === id)
    if (!u) return
    try {
      await apiPut(`/admin/users/${id}`, { status: u.status === 'active' ? 'disabled' : 'active' })
      await load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '状态更新失败')
    }
  }

  async function createUser() {
    if (!form.username.trim()) return
    try {
      await apiPost('/admin/users', {
        username: form.username.trim(),
        password: form.password,
        role: form.role,
      })
      setForm({ username: '', password: '', role: 'user' })
      setCreateOpen(false)
      await load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '创建失败')
    }
  }

  const todayCount = users.filter(
    (u) => u.createdAt === new Date().toLocaleDateString('zh-CN'),
  ).length

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

      {error && (
        <p className="text-sm text-state-archived mb-4" role="alert">
          {error}
        </p>
      )}

      {/* 统计卡（真实 users 表） */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <Users className="size-4 text-ink-faint" />
              <span className="text-xs font-mono text-ink-faint">全量</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">{total}</div>
            <div className="text-xs text-ink-muted mt-1">总用户数（真实）</div>
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
              <span className="text-xs font-mono text-state-emerging">+{todayCount}</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">{todayCount}</div>
            <div className="text-xs text-ink-muted mt-1">今日新增</div>
          </CardContent>
        </Card>
      </div>

      {/* 用户列表（真实） */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center justify-between">
            <span>用户列表</span>
            <span className="text-xs font-normal text-ink-faint">{users.length} 条</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="py-8 text-center text-sm text-ink-muted">加载中…</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>用户名</TableHead>
                  <TableHead>角色</TableHead>
                  <TableHead>状态</TableHead>
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
                      <TableCell className="text-xs font-mono text-ink-muted">{u.createdAt}</TableCell>
                      <TableCell>
                        <div className="flex items-center justify-end gap-2">
                          <Select value={u.role} onValueChange={(v) => setRole(u.id, v as Role)} disabled={isSelf(u.id)}>
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
                            disabled={isSelf(u.id)}
                            title={isSelf(u.id) ? '不能操作当前登录账户' : undefined}
                          >
                            {u.status === 'active' ? '禁用' : '启用'}
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
                {users.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={5} className="text-center text-sm text-ink-faint py-8">
                      暂无用户
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}
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
                placeholder="至少 6 字符"
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
            <Button disabled={!form.username.trim() || form.password.length < 6} onClick={createUser}>
              创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
