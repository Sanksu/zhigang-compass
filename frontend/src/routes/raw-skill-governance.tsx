import { useCallback, useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { apiGet, apiPost, apiPut } from '@/lib/api'
import { useSkillDescriptions } from '@/hooks/use-skill-descriptions'

interface SkillGovItem {
  name: string
}

/** 原始数据页「技能」页签：仅技能解释（展示 / 编辑 / LLM 补齐）。 */
export function RawSkillGovernance() {
  const { descMap, reloadDescs } = useSkillDescriptions()
  const [q, setQ] = useState('')
  const [skills, setSkills] = useState<SkillGovItem[]>([])
  const [backfilling, setBackfilling] = useState(false)
  const [loading, setLoading] = useState(true)
  const [notice, setNotice] = useState<string | null>(null)

  // 技能解释编辑对话框
  const [editing, setEditing] = useState<{ name: string; current?: string } | null>(null)
  const [editText, setEditText] = useState('')
  const [saving, setSaving] = useState(false)
  // LLM 补齐确认对话框
  const [confirmBackfill, setConfirmBackfill] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    const params = new URLSearchParams({ page: '1', size: '100' })
    if (q.trim()) params.set('q', q.trim())
    apiGet<{ items: SkillGovItem[] }>(`/admin/skills?${params}`)
      .then((s) => setSkills(s.items))
      .catch(() => setSkills([]))
      .finally(() => setLoading(false))
  }, [q])
  useEffect(() => {
    reloadDescs()
    // 微任务调度：避免 effect 内同步 setState（react-hooks/set-state-in-effect）
    void Promise.resolve().then(load)
  }, [q, reloadDescs, load])

  const openEdit = (name: string, current?: string) => {
    setEditing({ name, current })
    setEditText(current ?? '')
  }
  const saveDesc = async () => {
    if (!editing) return
    const text = editText.trim()
    if (!text) {
      setNotice('解释不能为空')
      return
    }
    setSaving(true)
    try {
      await apiPut(`/admin/skill-descriptions/${encodeURIComponent(editing.name)}`, { description: text })
      reloadDescs()
      setEditing(null)
      setNotice('已保存')
    } catch {
      setNotice('保存失败')
    } finally {
      setSaving(false)
    }
  }
  const runBackfill = async () => {
    setConfirmBackfill(false)
    setBackfilling(true)
    setNotice(null)
    try {
      const r = await apiPost<{ generated: number; failed: number }>('/admin/skill-descriptions/backfill')
      setNotice(`LLM 补齐完成：生成 ${r.generated} 条${r.failed ? `，失败 ${r.failed} 条` : ''}`)
      reloadDescs()
    } catch {
      setNotice('触发失败')
    } finally {
      setBackfilling(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">技能解释</h3>
        <span className="text-[11px] text-ink-faint">展示 / 编辑 / LLM 补齐</span>
        <Button size="sm" variant="outline" className="ml-auto h-8 text-xs" disabled={backfilling} onClick={() => setConfirmBackfill(true)}>
          {backfilling ? '补齐中…' : 'LLM 补齐空解释'}
        </Button>
      </div>

        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="按技能名过滤" className="h-8 w-64 text-sm" />

        {notice && <p className="text-xs text-ink-secondary">{notice}</p>}

        {loading ? (
          <p className="py-8 text-center text-sm text-ink-muted"><Loader2 className="mx-auto size-4 animate-spin" /></p>
        ) : skills.length === 0 ? (
          <p className="py-8 text-center text-sm text-ink-faint">暂无技能记录</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-40">技能名</TableHead>
                <TableHead>技能解释</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {skills.map((it) => {
                const d = descMap[it.name]
                return (
                  <TableRow key={it.name}>
                    <TableCell className="font-medium">{it.name}</TableCell>
                    <TableCell className="max-w-xl">
                      <div className="flex items-center gap-1.5">
                        <p className="flex-1 truncate text-xs text-ink-secondary" title={d?.override ?? d?.builtin ?? undefined}>
                          {d?.override ?? d?.builtin ?? '（空）'}
                        </p>
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-6 shrink-0 px-2 text-[11px]"
                          onClick={() => openEdit(it.name, d?.override)}
                        >
                          编辑
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}

        {/* 编辑技能解释 */}
        <Dialog open={editing !== null} onOpenChange={(o) => !o && setEditing(null)}>
          <DialogContent className="max-w-xl">
            <DialogHeader>
              <DialogTitle>编辑技能解释：{editing?.name}</DialogTitle>
            </DialogHeader>
            <textarea
              className="h-32 w-full resize-y rounded-md border border-border bg-canvas p-2.5 text-[13px] leading-relaxed text-ink-secondary focus:outline-none focus:ring-1 focus:ring-primary"
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              placeholder="输入该技能的解释说明"
            />
            <DialogFooter className="gap-2">
              <Button size="sm" variant="ghost" disabled={saving} onClick={() => setEditing(null)}>取消</Button>
              <Button size="sm" disabled={saving} onClick={saveDesc}>{saving ? '保存中…' : '保存'}</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* LLM 补齐确认 */}
        <Dialog open={confirmBackfill} onOpenChange={(o) => !o && setConfirmBackfill(false)}>
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle>LLM 补齐空解释</DialogTitle>
            </DialogHeader>
            <p className="text-sm text-ink-secondary">对解释为空的技能批量调用 LLM 生成解释，该操作会调用 LLM 并耗时较久。是否继续？</p>
            <DialogFooter className="gap-2">
              <Button size="sm" variant="ghost" onClick={() => setConfirmBackfill(false)}>取消</Button>
              <Button size="sm" onClick={runBackfill}>开始补齐</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
    </div>
  )
}