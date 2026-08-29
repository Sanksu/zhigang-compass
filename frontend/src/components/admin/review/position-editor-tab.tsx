import { useState } from 'react'
import { Plus, Save, Search, Trash2 } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge, type BadgeProps } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
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
import { apiGet, apiPut, errMsg } from '@/lib/api'
import type { Schema } from './review-types'

/** 编辑表单技能行（PUT 提交形状：name/necessity/weight，不含只读 level）。
 *  key 为行内稳定 id（第八轮 P2-36）：可增删行的渲染 key，删除中间行后
 *  React 按 key 复用 DOM，输入焦点不错位；save() 显式映射时剔除，不入请求体 */
interface SkillFormRow {
  key: string
  name: string
  necessity: 'must' | 'nice'
  weight: number
}

/** 行 id 生成：模块级单调计数，跨加载/新增全局唯一（新增行不得与存量行撞 key） */
let skillRowSeq = 0
function nextRowKey(): string {
  skillRowSeq += 1
  return `skill-row-${skillRowSeq}`
}

type PositionDetail = Schema['PositionEditDetail']

/**
 * 岗位人工编辑 Tab — 设计文档 §12.2
 *
 * 后端契约（backend/openapi/openapi.yaml）：
 * - GET /admin/positions/{name} 岗位详情：skills[{name, necessity, weight}] / core_duties / scenarios 等
 * - PUT /admin/positions/{name} 技能全量替换（necessity ∈ must|nice，weight ∈ 0-1）+ 文本字段更新，
 *   实际变更写入 PositionEditLog；返回 {position_name, updated, diff_summary}
 */
export function PositionEditorTab() {
  const [positionName, setPositionName] = useState('')
  const [detail, setDetail] = useState<PositionDetail | null>(null)
  const [skills, setSkills] = useState<SkillFormRow[]>([])
  const [coreDuties, setCoreDuties] = useState('')
  const [scenarios, setScenarios] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [diffSummary, setDiffSummary] = useState<string | null>(null)

  async function loadDetail() {
    const name = positionName.trim()
    if (!name) {
      setNotice('请输入岗位名')
      return
    }
    setLoading(true)
    setNotice(null)
    setDiffSummary(null)
    try {
      const d = await apiGet<PositionDetail>(`/admin/positions/${encodeURIComponent(name)}`)
      setDetail(d)
      setSkills(d.skills.map((s) => ({ key: nextRowKey(), name: s.name, necessity: s.necessity, weight: s.weight })))
      setCoreDuties(d.core_duties.join('\n'))
      setScenarios(d.scenarios.join('\n'))
    } catch (e) {
      setDetail(null)
      setNotice(errMsg(e, '岗位详情加载失败'))
    } finally {
      setLoading(false)
    }
  }

  function updateSkill(index: number, patch: Partial<SkillFormRow>) {
    setSkills((rows) => rows.map((r, i) => (i === index ? { ...r, ...patch } : r)))
  }

  async function save() {
    if (!detail) return
    const cleaned = skills
      .map((s) => ({ name: s.name.trim(), necessity: s.necessity, weight: Number(s.weight) }))
      .filter((s) => s.name)
    if (cleaned.some((s) => s.weight < 0 || s.weight > 1)) {
      setNotice('技能 weight 必须在 0.0-1.0 之间')
      return
    }
    setSaving(true)
    setNotice(null)
    setDiffSummary(null)
    try {
      const res = await apiPut<Schema['PositionEditResult']>(
        `/admin/positions/${encodeURIComponent(detail.name)}`,
        {
          skills: cleaned,
          core_duties: coreDuties.split('\n').map((x) => x.trim()).filter(Boolean),
          scenarios: scenarios.split('\n').map((x) => x.trim()).filter(Boolean),
        },
      )
      setNotice(res.updated ? '已保存编辑（变更已写入 PositionEditLog）' : '无变更（未写入编辑日志）')
      setDiffSummary(res.diff_summary || null)
    } catch (e) {
      setNotice(errMsg(e, '保存失败，请重试'))
    } finally {
      setSaving(false)
    }
  }

  const statusVariant = (status: string): BadgeProps['variant'] =>
    status === 'candidate' || status === 'emerging' || status === 'stable' || status === 'declining' || status === 'archived'
      ? status
      : 'outline'

  return (
    <div className="space-y-4">
      {notice && (
        <div className="rounded-md border border-state-emerging/20 bg-state-emerging/5 px-4 py-3 text-sm text-state-emerging">
          {notice}
        </div>
      )}

      {/* 岗位查找 */}
      <Card>
        <CardContent className="py-4 flex items-center gap-2">
          <Input
            value={positionName}
            onChange={(e) => setPositionName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && loadDetail()}
            placeholder="输入岗位名后加载详情（如：提示词工程师）"
            className="max-w-sm"
          />
          <Button size="sm" onClick={loadDetail} disabled={loading}>
            <Search className="size-3.5 mr-1" />
            {loading ? '加载中…' : '加载详情'}
          </Button>
          <span className="text-[12px] text-ink-faint">
            技能全量替换 / 文本定义更新，实际变更写入 PositionEditLog
          </span>
        </CardContent>
      </Card>

      {detail && (
        <>
          {/* 基本信息（只读） */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm flex items-center justify-between">
                <span>{detail.name}</span>
                <div className="flex items-center gap-2">
                  {detail.has_edit_log && (
                    <Badge variant="verified" className="text-[11px]" title="该岗位存在人工编辑记录（PositionEditLog）">
                      已人工校验
                    </Badge>
                  )}
                  <Badge variant={statusVariant(detail.status)} className="text-[11px]">{detail.status}</Badge>
                  <Badge variant="outline" className="text-[11px]">{detail.level || '—'}</Badge>
                  <Badge variant="outline" className="text-[11px]">{detail.industry || '—'}</Badge>
                  <Badge variant="outline" className="text-[11px]">{detail.salary_range || '—'}</Badge>
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent className="text-xs text-ink-muted flex flex-wrap gap-x-6 gap-y-1">
              <span>学历要求：{detail.education.length ? detail.education.map((e) => e.name).join('、') : '—'}</span>
              <span>证书要求：{detail.certifications.length ? detail.certifications.map((c) => c.name).join('、') : '—'}</span>
              <span>最近更新：{detail.updated_at || '—'}</span>
            </CardContent>
          </Card>

          {/* 技能编辑（全量替换） */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm flex items-center justify-between">
                <span>技能要求（{skills.length}）</span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setSkills((rows) => [...rows, { key: nextRowKey(), name: '', necessity: 'must', weight: 1 }])}
                >
                  <Plus className="size-3.5 mr-1" />
                  添加技能
                </Button>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {skills.length === 0 ? (
                <p className="py-8 text-center text-sm text-ink-faint">该岗位暂无技能要求，可点击「添加技能」</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>技能名</TableHead>
                      <TableHead className="w-40">necessity</TableHead>
                      <TableHead className="w-32">weight（0-1）</TableHead>
                      <TableHead className="w-10" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {skills.map((s, i) => (
                      <TableRow key={s.key}>
                        <TableCell>
                          <Input
                            value={s.name}
                            onChange={(e) => updateSkill(i, { name: e.target.value })}
                            className="h-8"
                          />
                        </TableCell>
                        <TableCell>
                          <Select
                            value={s.necessity}
                            onValueChange={(v) => updateSkill(i, { necessity: v as SkillFormRow['necessity'] })}
                          >
                            <SelectTrigger className="h-8">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="must">must（必备）</SelectItem>
                              <SelectItem value="nice">nice（加分）</SelectItem>
                            </SelectContent>
                          </Select>
                        </TableCell>
                        <TableCell>
                          <Input
                            type="number"
                            min={0}
                            max={1}
                            step={0.1}
                            value={s.weight}
                            onChange={(e) => updateSkill(i, { weight: Number(e.target.value) })}
                            className="h-8"
                          />
                        </TableCell>
                        <TableCell>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-ink-faint hover:text-state-archived"
                            onClick={() => setSkills((rows) => rows.filter((_, idx) => idx !== i))}
                          >
                            <Trash2 className="size-3.5" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          {/* 文本定义编辑 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">核心职责（core_duties，每行一条）</CardTitle>
              </CardHeader>
              <CardContent>
                <Textarea value={coreDuties} onChange={(e) => setCoreDuties(e.target.value)} rows={6} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">应用场景（scenarios，每行一条）</CardTitle>
              </CardHeader>
              <CardContent>
                <Textarea value={scenarios} onChange={(e) => setScenarios(e.target.value)} rows={6} />
              </CardContent>
            </Card>
          </div>

          {/* 保存 */}
          <div className="flex items-center gap-3">
            <Button size="sm" onClick={save} disabled={saving}>
              <Save className="size-3.5 mr-1" />
              {saving ? '保存中…' : '保存编辑'}
            </Button>
            {diffSummary && <span className="text-xs text-ink-secondary break-all">{diffSummary}</span>}
          </div>
        </>
      )}
    </div>
  )
}
