import { useEffect, useState } from 'react'
import { Cpu, Plus, Save } from 'lucide-react'
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
import { apiGet, apiPut } from '@/lib/api'
import type { components } from '@/types/api'

/** LLM provider 配置（契约 LlmProviderConfig，/admin/llm-config） */
type LlmProviderConfig = components['schemas']['LlmProviderConfig']

type LlmConfig = components['schemas']['LlmConfig']

interface FormState extends LlmProviderConfig {
  api_key: string
}

const EMPTY_FORM: FormState = {
  name: '',
  priority: 1,
  base_url: '',
  api_key: '',
  model: '',
  supports_function_calling: true,
  enabled: true,
}

type Feedback = { type: 'ok' | 'err'; text: string } | null

export function AdminLlmPage() {
  const [providers, setProviders] = useState<LlmProviderConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editIndex, setEditIndex] = useState<number | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)

  // 初始加载：promise 链内 setState（避免 set-state-in-effect 级联渲染）
  useEffect(() => {
    let cancelled = false
    apiGet<LlmConfig>('/admin/llm-config')
      .then((cfg) => {
        if (cancelled) return
        setProviders(cfg.providers ?? [])
      })
      .catch((err) => {
        if (!cancelled) setFeedback({ type: 'err', text: err instanceof Error ? err.message : '配置加载失败' })
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  function openCreate() {
    setEditIndex(null)
    setForm(EMPTY_FORM)
    setDialogOpen(true)
  }

  function openEdit(index: number) {
    setEditIndex(index)
    const p = providers[index]
    setForm({ ...p, api_key: '' }) // 编辑时 api_key 留空，保存保持原值
    setDialogOpen(true)
  }

  function submitForm() {
    const entry: FormState = { ...form, name: form.name.trim(), base_url: form.base_url.trim(), model: form.model.trim() }
    if (editIndex === null) {
      setProviders((prev) => [...prev, entry])
    } else {
      setProviders((prev) => prev.map((p, i) => (i === editIndex ? entry : p)))
    }
    setDialogOpen(false)
  }

  function removeProvider(index: number) {
    setProviders((prev) => prev.filter((_, i) => i !== index))
  }

  async function saveAll() {
    setSaving(true)
    setFeedback(null)
    try {
      const saved = await apiPut<LlmConfig>('/admin/llm-config', { providers })
      setProviders(saved.providers ?? [])
      setFeedback({ type: 'ok', text: '已持久化到 llm_providers.yaml' })
    } catch (err) {
      setFeedback({ type: 'err', text: err instanceof Error ? err.message : '保存失败' })
    } finally {
      setSaving(false)
    }
  }

  const sorted = [...providers].sort((a, b) => a.priority - b.priority)

  return (
    <>
      <PageHeader
        title="LLM Provider 配置"
        description="多 provider 同步重试链 · 运行时配置 · 持久化到 llm_providers.yaml"
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={openCreate}>
              <Plus className="size-4" />
              新增 Provider
            </Button>
            <Button onClick={saveAll} disabled={saving || providers.length === 0}>
              <Save className="size-4" />
              {saving ? '保存中…' : '保存配置'}
            </Button>
          </div>
        }
      />

      {/* 安全与范围提示 */}
      <div className="mb-6 flex items-start gap-2 rounded-md border border-border bg-subtle px-4 py-3 text-xs text-ink-secondary leading-relaxed">
        <Cpu className="size-4 mt-0.5 shrink-0" />
        <span>
          api_key 打码展示、不明文回显；留空或保持掩码即维持原值。优先级数字越小越先尝试，且不可重复。
          超时/限流/降级/恢复等高级参数在 yaml 中维护，不在此页面编辑。
        </span>
      </div>

      {feedback && (
        <div
          className={`mb-4 rounded-md px-4 py-2 text-sm ${
            feedback.type === 'ok' ? 'bg-emerald-500/10 text-emerald-600' : 'bg-red-500/10 text-red-600'
          }`}
        >
          {feedback.text}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center justify-between">
            <span>Provider 列表</span>
            <span className="text-xs font-normal text-ink-faint">{providers.length} 个</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="py-8 text-center text-sm text-ink-muted">加载中…</div>
          ) : sorted.length === 0 ? (
            <div className="py-8 text-center text-sm text-ink-muted">暂无 Provider，点击「新增 Provider」添加</div>
          ) : (
            <>
              {/* 桌面端：表格视图 */}
              <div className="hidden lg:block">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-16">优先级</TableHead>
                      <TableHead>名称</TableHead>
                      <TableHead>模型</TableHead>
                      <TableHead className="max-w-52">Base URL</TableHead>
                      <TableHead className="w-28">API Key</TableHead>
                      <TableHead className="w-20">状态</TableHead>
                      <TableHead className="text-right">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {sorted.map((p) => {
                      const originalIndex = providers.findIndex((x) => x === p)
                      return (
                        <TableRow key={p.name}>
                          <TableCell className="font-mono text-xs">{p.priority}</TableCell>
                          <TableCell className="font-medium font-mono">{p.name}</TableCell>
                          <TableCell className="font-mono text-xs">{p.model}</TableCell>
                          <TableCell className="text-xs font-mono text-ink-muted truncate max-w-52">{p.base_url}</TableCell>
                          <TableCell className="text-xs font-mono">{p.api_key ? `${p.api_key.slice(-4)}` : '未配置'}</TableCell>
                          <TableCell>
                            <Badge variant={p.enabled === false ? 'archived' : 'emerging'}>
                              {p.enabled === false ? '停用' : '启用'}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center justify-end gap-2">
                              <Button size="sm" variant="outline" onClick={() => openEdit(originalIndex)}>
                                编辑
                              </Button>
                              <Button size="sm" variant="outline" onClick={() => removeProvider(originalIndex)}>
                                删除
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </div>

              {/* 移动端：卡片视图 */}
              <div className="space-y-3 lg:hidden">
                {sorted.map((p) => {
                  const originalIndex = providers.findIndex((x) => x === p)
                  return (
                    <div
                      key={p.name}
                      className="rounded-lg border border-border bg-canvas p-4 space-y-3"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="font-medium font-mono text-ink">{p.name}</span>
                            <Badge variant={p.enabled === false ? 'archived' : 'emerging'}>
                              {p.enabled === false ? '停用' : '启用'}
                            </Badge>
                          </div>
                          <div className="mt-1 text-xs font-mono text-ink-faint">
                            #{p.priority} · {p.model}
                          </div>
                        </div>
                      </div>
                      <div className="text-xs font-mono text-ink-muted truncate" title={p.base_url}>
                        {p.base_url}
                      </div>
                      <div className="text-xs text-ink-faint">
                        API Key：{p.api_key ? `••••${p.api_key.slice(-4)}` : '未配置'}
                      </div>
                      <div className="flex flex-wrap gap-1.5 pt-1">
                        <Button size="sm" variant="outline" onClick={() => openEdit(originalIndex)}>
                          编辑
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => removeProvider(originalIndex)}>
                          删除
                        </Button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* 编辑 / 新增 Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editIndex === null ? '新增 Provider' : '编辑 Provider'}</DialogTitle>
            <DialogDescription>配置会持久化到 configs/llm_providers.yaml</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>名称</Label>
              <Input
                value={form.name}
                disabled={editIndex !== null}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="deepseek（字母/数字/下划线/短横线）"
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>优先级</Label>
                <Input
                  type="number"
                  min={1}
                  value={form.priority}
                  onChange={(e) => setForm((f) => ({ ...f, priority: Number(e.target.value) }))}
                />
              </div>
              <div className="space-y-1.5">
                <Label>状态</Label>
                <Select
                  value={form.enabled ? 'enabled' : 'disabled'}
                  onValueChange={(v) => setForm((f) => ({ ...f, enabled: v === 'enabled' }))}
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="enabled">启用</SelectItem>
                    <SelectItem value="disabled">停用</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>Base URL</Label>
              <Input
                value={form.base_url}
                onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
                placeholder="https://api.deepseek.com/v1"
              />
            </div>
            <div className="space-y-1.5">
              <Label>模型</Label>
              <Input
                value={form.model}
                onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
                placeholder="deepseek-v4-flash"
              />
            </div>
            <div className="space-y-1.5">
              <Label>API Key{editIndex !== null && <span className="text-ink-faint font-normal">（留空保持原值）</span>}</Label>
              <Input
                type="password"
                value={form.api_key}
                onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
                placeholder={editIndex !== null ? '••••••••（不修改请留空）' : 'sk-…'}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Function Calling</Label>
              <Select
                value={form.supports_function_calling ? 'yes' : 'no'}
                onValueChange={(v) => setForm((f) => ({ ...f, supports_function_calling: v === 'yes' }))}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="yes">支持</SelectItem>
                  <SelectItem value="no">不支持</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>取消</Button>
            <Button onClick={submitForm} disabled={!form.name.trim() || !form.base_url.trim() || !form.model.trim()}>
              {editIndex === null ? '添加' : '更新'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
