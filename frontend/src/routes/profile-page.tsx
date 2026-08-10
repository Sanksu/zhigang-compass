import { useCallback, useEffect, useState } from 'react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from '@/components/ui/table'
import type { ResumeSummary } from '@/components/match/types'
import { apiDelete, apiGet, apiPost, apiPut, ApiError, getAccessToken } from '@/lib/api'

/** /auth/me 返回的用户资料 */
interface MeProfile {
  id: string
  username: string
  role: string
  email: string
  phone: string
  bio: string
  created_at: string | null
}

const ROLE_LABEL: Record<string, string> = { admin: '管理员', user: '用户', guest: '访客' }

export function ProfilePage() {
  /* ── 用户资料（/auth/me 真实数据） ── */
  const [profile, setProfile] = useState<MeProfile | null>(null)
  const [loading, setLoading] = useState(true)

  /* 个人信息编辑 */
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [bio, setBio] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  /* 修改密码 */
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [pwdError, setPwdError] = useState<string | null>(null)
  const [pwdSuccess, setPwdSuccess] = useState(false)
  const [pwdSubmitting, setPwdSubmitting] = useState(false)

  /* 简历管理（/resume/list 真实数据） */
  const [resumes, setResumes] = useState<ResumeSummary[]>([])
  const [dialogOpen, setDialogOpen] = useState(false)
  const [selectedName, setSelectedName] = useState('')
  const [selectedParsed, setSelectedParsed] = useState<Record<string, unknown> | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  /* 简历编辑（PUT /resume/{id}，编辑 skills 字段） */
  const [editOpen, setEditOpen] = useState(false)
  const [editId, setEditId] = useState<string | null>(null)
  const [editSkills, setEditSkills] = useState('')
  const [savingEdit, setSavingEdit] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)

  function showToast(msg: string) {
    setToast(msg)
    setTimeout(() => setToast(null), 3000)
  }

  /* ── 加载用户资料 + 简历列表 ── */
  const loadProfile = useCallback(async () => {
    try {
      const me = await apiGet<MeProfile>('/auth/me')
      setProfile(me)
      setEmail(me.email ?? '')
      setPhone(me.phone ?? '')
      setBio(me.bio ?? '')
    } catch {
      /* 未登录场景由 AuthGuard 兜底，这里保持空态 */
    }
  }, [])

  const loadResumes = useCallback(async () => {
    try {
      const data = await apiGet<{ items: ResumeSummary[]; total: number }>('/resume/list')
      setResumes(data.items)
    } catch {
      setResumes([])
    }
  }, [])

  useEffect(() => {
    // 异步加载用户资料 + 简历列表；setState 均发生在 await 之后的回调中，
    // 避免在 effect 体内同步调用 setState 触发级联渲染（react-hooks/set-state-in-effect）
    void (async () => {
      await Promise.all([loadProfile(), loadResumes()])
      setLoading(false)
    })()
  }, [loadProfile, loadResumes])

  /* ── 保存个人信息 ── */
  async function handleSaveProfile() {
    setSaving(true)
    setSaveError(null)
    try {
      const updated = await apiPut<MeProfile>('/auth/me', { email, phone, bio })
      setProfile(updated)
      showToast('已保存')
    } catch (e) {
      setSaveError(e instanceof ApiError ? e.message : '保存失败，请重试')
    } finally {
      setSaving(false)
    }
  }

  /* ── 修改密码 ── */
  async function handleChangePassword() {
    setPwdError(null)
    setPwdSuccess(false)

    if (!oldPwd || !newPwd || !confirmPwd) {
      setPwdError('请填写所有密码字段')
      return
    }
    if (newPwd !== confirmPwd) {
      setPwdError('新密码与确认密码不一致')
      return
    }
    if (newPwd.length < 6) {
      setPwdError('新密码长度不少于 6 位')
      return
    }
    setPwdSubmitting(true)
    try {
      await apiPost('/auth/password', { old_password: oldPwd, new_password: newPwd })
      setPwdSuccess(true)
      setOldPwd('')
      setNewPwd('')
      setConfirmPwd('')
    } catch (e) {
      setPwdError(e instanceof ApiError ? e.message : '密码修改失败，请重试')
    } finally {
      setPwdSubmitting(false)
    }
  }

  /* ── 简历查看（拉取完整画像） ── */
  async function handleViewResume(r: ResumeSummary) {
    setSelectedName(r.file_name)
    setSelectedParsed(null)
    setDialogOpen(true)
    setDetailLoading(true)
    try {
      const detail = await apiGet<{ parsed_data: Record<string, unknown> }>(`/resume/${r.id}`)
      setSelectedParsed(detail.parsed_data ?? {})
    } catch {
      setSelectedParsed(null)
    } finally {
      setDetailLoading(false)
    }
  }

  /* ── 简历删除 ── */
  async function handleDeleteResume(id: string) {
    if (!window.confirm('确认删除该简历？删除后不可恢复')) return
    setDeletingId(id)
    try {
      await apiDelete(`/resume/${id}`)
      setResumes((prev) => prev.filter((r) => r.id !== id))
      showToast('已删除')
    } catch (e) {
      showToast(e instanceof ApiError ? e.message : '删除失败')
    } finally {
      setDeletingId(null)
    }
  }

  /* ── 简历原文下载（GET /resume/files/{id}/download，二进制非 ApiResponse） ── */
  async function handleDownloadResume(r: ResumeSummary) {
    setDeletingId(r.id)
    try {
      const token = getAccessToken()
      const resp = await fetch(`/api/v1/resume/files/${r.id}/download`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!resp.ok) {
        const body = await resp.json().catch(() => null)
        throw new Error(body?.msg ?? `下载失败（HTTP ${resp.status}）`)
      }
      const blob = await resp.blob()
      const disposition = resp.headers.get('Content-Disposition') ?? ''
      // 后端 RFC 5987 filename*：取 filename* 或 filename 作为保存名
      const match = disposition.match(/filename\*=utf-8''([^;]+)/i)
      const filename = match ? decodeURIComponent(match[1]) : r.file_name
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      showToast(e instanceof Error ? e.message : '下载失败')
    } finally {
      setDeletingId(null)
    }
  }

  /* ── 简历编辑：拉取当前 skills 并打开编辑框 ── */
  async function handleEditResume(r: ResumeSummary) {
    setEditId(r.id)
    setEditError(null)
    setEditSkills((r.skills ?? []).join('、'))
    setEditOpen(true)
  }

  /* ── 简历编辑：PUT /resume/{id} 顶层覆盖 fields.skills ── */
  async function handleSaveEdit() {
    if (!editId) return
    setSavingEdit(true)
    setEditError(null)
    try {
      const skills = editSkills
        .split(/[、,，]/)
        .map((s) => s.trim())
        .filter(Boolean)
      await apiPut(`/resume/${editId}`, { fields: { skills } })
      setEditOpen(false)
      showToast('简历已更新')
      await loadResumes()
    } catch (e) {
      setEditError(e instanceof ApiError ? e.message : '保存失败，请重试')
    } finally {
      setSavingEdit(false)
    }
  }

  const roleLabel = profile ? ROLE_LABEL[profile.role] ?? profile.role : ''

  return (
    <div className="space-y-6">
      <PageHeader title="个人中心" description="账户信息与简历管理" />

      {/* Toast 通知 */}
      {toast && (
        <div className="rounded-md border border-state-candidate/20 bg-state-candidate/5 px-4 py-3 text-sm text-state-candidate">
          {toast}
        </div>
      )}

      {loading ? (
        <p className="py-8 text-center text-sm text-ink-muted">加载中…</p>
      ) : (
        <>
          {/* ── 顶部用户摘要卡 ── */}
          <Card>
            <CardContent className="flex items-center gap-6 p-6">
              <div className="flex size-16 shrink-0 items-center justify-center rounded-full bg-ink text-xl font-semibold text-canvas">
                {(profile?.username ?? '?').charAt(0)}
              </div>
              <div className="min-w-0 flex-1 space-y-2">
                <div className="flex items-center gap-3">
                  <h2 className="text-lg font-semibold text-ink">{profile?.username ?? '—'}</h2>
                  {roleLabel && <Badge variant={profile?.role === 'admin' ? 'default' : 'outline'}>{roleLabel}</Badge>}
                  {profile?.created_at && (
                    <span className="text-xs text-ink-muted">
                      注册于 {profile.created_at.slice(0, 10)}
                    </span>
                  )}
                </div>
                <div className="flex gap-6 text-sm text-ink-secondary">
                  <span>简历数 {resumes.length}</span>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* ── 个人信息编辑区 ── */}
          <Card>
            <CardHeader>
              <CardTitle>基本信息</CardTitle>
              <CardDescription>编辑你的个人资料信息</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3">
                <Label htmlFor="profile-username">用户名</Label>
                <Input id="profile-username" value={profile?.username ?? ''} disabled />
              </div>
              <div className="grid gap-3">
                <Label htmlFor="profile-email">邮箱</Label>
                <Input id="profile-email" value={email} onChange={(e) => setEmail(e.target.value)} />
              </div>
              <div className="grid gap-3">
                <Label htmlFor="profile-phone">联系电话</Label>
                <Input id="profile-phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
              </div>
              <div className="grid gap-3">
                <Label htmlFor="profile-bio">个人简介</Label>
                <Textarea id="profile-bio" value={bio} onChange={(e) => setBio(e.target.value)} rows={3} />
              </div>
              {saveError && <p className="text-sm text-state-archived">{saveError}</p>}
              <Button onClick={handleSaveProfile} disabled={saving}>
                {saving ? '保存中…' : '保存修改'}
              </Button>
            </CardContent>
          </Card>

          {/* ── 修改密码区 ── */}
          <Card>
            <CardHeader>
              <CardTitle>修改密码</CardTitle>
              <CardDescription>定期更换密码可以提高账户安全性</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3">
                <Label htmlFor="old-pwd">原密码</Label>
                <Input id="old-pwd" type="password" value={oldPwd} onChange={(e) => setOldPwd(e.target.value)} />
              </div>
              <div className="grid gap-3">
                <Label htmlFor="new-pwd">新密码</Label>
                <Input id="new-pwd" type="password" value={newPwd} onChange={(e) => setNewPwd(e.target.value)} />
              </div>
              <div className="grid gap-3">
                <Label htmlFor="confirm-pwd">确认新密码</Label>
                <Input id="confirm-pwd" type="password" value={confirmPwd} onChange={(e) => setConfirmPwd(e.target.value)} />
              </div>
              {pwdError && <p className="text-sm text-state-archived">{pwdError}</p>}
              {pwdSuccess && <p className="text-sm text-state-candidate">密码修改成功</p>}
              <Button onClick={handleChangePassword} disabled={pwdSubmitting}>
                {pwdSubmitting ? '提交中…' : '修改密码'}
              </Button>
            </CardContent>
          </Card>

          {/* ── 简历管理列表 ── */}
          <Card>
            <CardHeader>
              <CardTitle>简历管理</CardTitle>
              <CardDescription>已上传的简历文件列表</CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>文件名</TableHead>
                    <TableHead>技能</TableHead>
                    <TableHead>更新时间</TableHead>
                    <TableHead className="text-right">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {resumes.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className="max-w-64 truncate font-medium text-ink">{r.file_name}</TableCell>
                      <TableCell className="text-ink-secondary">
                        {(r.skills ?? []).slice(0, 5).join('、') || '—'}
                      </TableCell>
                      <TableCell className="text-ink-secondary">
                        {r.updated_at ? r.updated_at.slice(0, 16).replace('T', ' ') : '—'}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-2">
                          <Button variant="ghost" size="sm" onClick={() => handleViewResume(r)}>
                            查看
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={deletingId === r.id}
                            onClick={() => handleDownloadResume(r)}
                          >
                            {deletingId === r.id ? '下载中…' : '下载'}
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => handleEditResume(r)}>
                            编辑
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={deletingId === r.id}
                            onClick={() => handleDeleteResume(r.id)}
                          >
                            {deletingId === r.id ? '删除中…' : '删除'}
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                  {resumes.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={4} className="text-center text-ink-muted">
                        暂无简历记录，可前往「简历匹配」页上传
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}

      {/* ── 简历查看 Dialog ── */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>简历解析结果</DialogTitle>
            <DialogDescription>{selectedName}</DialogDescription>
          </DialogHeader>
          {detailLoading ? (
            <p className="text-sm text-ink-muted">加载中…</p>
          ) : selectedParsed ? (
            <pre className="max-h-80 overflow-auto rounded border border-border bg-subtle p-4 text-sm text-ink-secondary">
              {JSON.stringify(selectedParsed, null, 2)}
            </pre>
          ) : (
            <p className="text-sm text-ink-muted">解析详情不可用。</p>
          )}
        </DialogContent>
      </Dialog>

      {/* ── 简历编辑 Dialog（PUT /resume/{id}） ── */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑简历技能</DialogTitle>
            <DialogDescription>修正 LLM 抽取的技能列表（逗号或顿号分隔）</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <Label htmlFor="edit-skills">技能列表</Label>
            <Textarea
              id="edit-skills"
              value={editSkills}
              onChange={(e) => setEditSkills(e.target.value)}
              rows={4}
              placeholder="Python、机器学习、深度学习"
            />
            {editError && <p className="text-sm text-state-archived">{editError}</p>}
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setEditOpen(false)}>
                取消
              </Button>
              <Button size="sm" onClick={handleSaveEdit} disabled={savingEdit}>
                {savingEdit ? '保存中…' : '保存'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
