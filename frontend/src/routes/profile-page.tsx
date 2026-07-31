import { useState } from 'react'
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

/* ── Mock 数据 ── */

interface ResumeRecord {
  id: string
  fileName: string
  uploadTime: string
  status: 'completed' | 'processing' | 'failed'
  parsedData: Record<string, unknown>
}

const mockUser = {
  username: '张明',
  role: 'admin' as const,
  email: 'zhangming@example.com',
  phone: '138-0000-0000',
  bio: '资深 HR 专家，专注于技术岗位人才评估与能力模型构建。',
  registeredAt: '2025-03-15',
  resumeCount: 3,
  matchCount: 28,
  lastLogin: '2026-07-29 14:32',
}

const mockResumes: ResumeRecord[] = [
  {
    id: '1',
    fileName: '前端高级工程师_李明_简历.pdf',
    uploadTime: '2026-07-28 10:15',
    status: 'completed',
    parsedData: {
      姓名: '李明',
      学历: '本科',
      工作年限: '8 年',
      技能: ['React', 'TypeScript', 'Node.js', '系统设计'],
      匹配岗位: '前端架构师',
      匹配度: '92%',
    },
  },
  {
    id: '2',
    fileName: '后端开发_王芳_简历.pdf',
    uploadTime: '2026-07-25 16:40',
    status: 'completed',
    parsedData: {
      姓名: '王芳',
      学历: '硕士',
      工作年限: '5 年',
      技能: ['Java', 'Spring Boot', '微服务', 'Kubernetes'],
      匹配岗位: '高级后端工程师',
      匹配度: '87%',
    },
  },
  {
    id: '3',
    fileName: '产品经理_赵岩_简历.pdf',
    uploadTime: '2026-07-20 09:00',
    status: 'processing',
    parsedData: {},
  },
]

/* ── 页面 ── */

export function ProfilePage() {
  /* 个人信息编辑 */
  const [email, setEmail] = useState(mockUser.email)
  const [phone, setPhone] = useState(mockUser.phone)
  const [bio, setBio] = useState(mockUser.bio)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  /* 修改密码 */
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [pwdError, setPwdError] = useState<string | null>(null)
  const [pwdSuccess, setPwdSuccess] = useState(false)

  /* 简历管理 */
  const [resumes, setResumes] = useState(mockResumes)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [selectedResume, setSelectedResume] = useState<ResumeRecord | null>(null)

  /* ── Toast 辅助 ── */
  function showToast(msg: string) {
    setToast(msg)
    setTimeout(() => setToast(null), 3000)
  }

  /* ── 保存个人信息 ── */
  async function handleSaveProfile() {
    setSaving(true)
    await new Promise((r) => setTimeout(r, 3000))
    setSaving(false)
    showToast('已保存')
  }

  /* ── 修改密码 ── */
  function handleChangePassword() {
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
    setPwdSuccess(true)
    setOldPwd('')
    setNewPwd('')
    setConfirmPwd('')
  }

  /* ── 简历查看 ── */
  function handleViewResume(r: ResumeRecord) {
    setSelectedResume(r)
    setDialogOpen(true)
  }

  /* ── 简历删除 ── */
  function handleDeleteResume(id: string) {
    if (window.confirm('确认删除该简历？')) {
      setResumes((prev) => prev.filter((r) => r.id !== id))
    }
  }

  const statusBadge: Record<ResumeRecord['status'], { label: string; variant: 'candidate' | 'emerging' | 'archived' }> = {
    completed: { label: '完成', variant: 'candidate' },
    processing: { label: '处理中', variant: 'emerging' },
    failed: { label: '失败', variant: 'archived' },
  }

  return (
    <div className="space-y-6">
      <PageHeader title="个人中心" description="账户信息与简历管理" />

      {/* Toast 通知 */}
      {toast && (
        <div className="rounded-md border border-state-candidate/20 bg-state-candidate/5 px-4 py-3 text-sm text-state-candidate">
          {toast}
        </div>
      )}

      {/* ── 顶部用户摘要卡 ── */}
      <Card>
        <CardContent className="flex items-center gap-6 p-6">
          <div className="flex size-16 shrink-0 items-center justify-center rounded-full bg-ink text-xl font-semibold text-canvas">
            {mockUser.username.charAt(0)}
          </div>
          <div className="min-w-0 flex-1 space-y-2">
            <div className="flex items-center gap-3">
              <h2 className="text-lg font-semibold text-ink">{mockUser.username}</h2>
              <Badge variant={mockUser.role === 'admin' ? 'default' : 'outline'}>
                {mockUser.role}
              </Badge>
              <span className="text-xs text-ink-muted">注册于 {mockUser.registeredAt}</span>
            </div>
            <div className="flex gap-6 text-sm text-ink-secondary">
              <span>简历数 {mockUser.resumeCount}</span>
              <span>匹配次数 {mockUser.matchCount}</span>
              <span>最后登录 {mockUser.lastLogin}</span>
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
            <Input id="profile-username" value={mockUser.username} disabled />
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
          <Button onClick={handleChangePassword}>修改密码</Button>
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
                <TableHead>上传时间</TableHead>
                <TableHead>解析状态</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {resumes.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="font-medium text-ink">{r.fileName}</TableCell>
                  <TableCell className="text-ink-secondary">{r.uploadTime}</TableCell>
                  <TableCell>
                    <Badge variant={statusBadge[r.status].variant}>{statusBadge[r.status].label}</Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button variant="ghost" size="sm" onClick={() => handleViewResume(r)}>
                        查看
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => handleDeleteResume(r.id)}>
                        删除
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {resumes.length === 0 && (
                <TableRow>
                  <TableCell colSpan={4} className="text-center text-ink-muted">
                    暂无简历记录
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* ── 简历查看 Dialog ── */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>简历解析结果</DialogTitle>
            <DialogDescription>{selectedResume?.fileName}</DialogDescription>
          </DialogHeader>
          {selectedResume?.status === 'completed' ? (
            <pre className="max-h-80 overflow-auto rounded border border-border bg-subtle p-4 text-sm text-ink-secondary">
              {JSON.stringify(selectedResume.parsedData, null, 2)}
            </pre>
          ) : selectedResume?.status === 'processing' ? (
            <p className="text-sm text-ink-muted">该简历正在解析中，请稍后再查看。</p>
          ) : (
            <p className="text-sm text-ink-muted">解析失败，无法查看内容。</p>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
