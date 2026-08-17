import { useEffect, useState } from 'react'
import {
  Plus,
  Save,
  Search,
  Trash2,
} from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge, type BadgeProps } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Input } from '@/components/ui/input'
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { apiGet, apiPut, errMsg } from '@/lib/api'
import type { components } from '@/types/api'
import { CandidateReviewTab } from '@/components/admin/review/candidate-review-tab'
import { EvolutionReviewTab } from '@/components/admin/review/evolution-review-tab'

type Schema = components['schemas']

/**
 * 岗位审核页 — 设计文档 §7.2.2 + AL-M4-01
 *
 * 候选晋升 / 演化审核 Tab 已拆至 components/admin/review/；
 * 岗位人工编辑 / 发现观察池 仍在同文件内联（后续拆分）。
 */
export function AdminReviewPage() {
  const [tab, setTab] = useState<'candidate' | 'evolution' | 'edit' | 'watch'>('candidate')

  return (
    <>
      <PageHeader
        title="岗位审核"
        description="六状态机全链路人工审核：候选晋升（candidate → emerging / rejected）· 演化晋级（emerging → stable / declining）· 衰退归档（declining → archived）"
      />
      <Tabs value={tab} onValueChange={(v) => setTab(v as 'candidate' | 'evolution' | 'edit' | 'watch')}>
        <TabsList>
          <TabsTrigger value="candidate" className="text-xs">候选晋升审核</TabsTrigger>
          <TabsTrigger value="evolution" className="text-xs">演化审核（emerging）</TabsTrigger>
          <TabsTrigger value="edit" className="text-xs">岗位人工编辑</TabsTrigger>
          <TabsTrigger value="watch" className="text-xs">发现观察池</TabsTrigger>
        </TabsList>
        <TabsContent value="candidate">
          <CandidateReviewTab />
        </TabsContent>
        <TabsContent value="evolution">
          <EvolutionReviewTab />
        </TabsContent>
        <TabsContent value="edit">
          <PositionEditorTab />
        </TabsContent>
        <TabsContent value="watch">
          <TechnologyWatchTab />
        </TabsContent>
      </Tabs>
    </>
  )
}

/**
 * 岗位人工编辑 Tab — 设计文档 §12.2
 *
 * 后端契约（backend/openapi/openapi.yaml）：
 * - GET /admin/positions/{name} 岗位详情：skills[{name, necessity, weight}] / core_duties / scenarios 等
 * - PUT /admin/positions/{name} 技能全量替换（necessity ∈ must|nice，weight ∈ 0-1）+ 文本字段更新，
 *   实际变更写入 PositionEditLog；返回 {position_name, updated, diff_summary}
 */
/** 编辑表单技能行（PUT 提交形状：name/necessity/weight，不含只读 level） */
interface SkillFormRow {
  name: string
  necessity: 'must' | 'nice'
  weight: number
}

type PositionDetail = Schema['PositionEditDetail']

function PositionEditorTab() {
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
      setSkills(d.skills.map((s) => ({ name: s.name, necessity: s.necessity, weight: s.weight })))
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
          <span className="text-[11px] text-ink-faint">
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
                  <Badge variant={statusVariant(detail.status)} className="text-[10px]">{detail.status}</Badge>
                  <Badge variant="outline" className="text-[10px]">{detail.level || '—'}</Badge>
                  <Badge variant="outline" className="text-[10px]">{detail.industry || '—'}</Badge>
                  <Badge variant="outline" className="text-[10px]">{detail.salary_range || '—'}</Badge>
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
                  onClick={() => setSkills((rows) => [...rows, { name: '', necessity: 'must', weight: 1 }])}
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
                      <TableRow key={i}>
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

/**
 * 发现观察池 Tab — 设计文档 §7.2.5（admin 周报可见）
 *
 * 数据源：真实 GET /admin/discovery/watch（技术热点信号列表，支持按 status/source 筛选）。
 * 展示技能信号周报：信号源 / 信号值 / 周期 / 状态（watch / candidate_promoted / archived）。
 */
type WatchRow = Schema['WatchItem']

const WATCH_SOURCE_LABEL: Record<string, string> = {
  jd: 'JD',
  arxiv: '论文',
  course: '课程',
  github: 'GitHub',
  community: '社区',
  stackoverflow: 'SO',
}

function TechnologyWatchTab() {
  const [items, setItems] = useState<WatchRow[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState('')

  const PAGE_SIZE = 50
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const load = (status = '', source = '', p = 1) => {
    const params = new URLSearchParams({ page: String(p), size: String(PAGE_SIZE) })
    if (status) params.set('status', status)
    if (source) params.set('source', source)
    apiGet<Schema['WatchData']>(`/admin/discovery/watch?${params}`)
      .then((res) => {
        setItems(res.items)
        setTotal(res.total)
      })
      .catch(() => setError('观察池加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [])

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Select value={statusFilter} onValueChange={(v) => { setStatusFilter(v); setPage(1); load(v, sourceFilter, 1) }}>
          <SelectTrigger className="w-36 h-8 text-xs">
            <SelectValue placeholder="状态筛选" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">全部状态</SelectItem>
            <SelectItem value="watch">观察中</SelectItem>
            <SelectItem value="candidate_promoted">候选提升</SelectItem>
            <SelectItem value="archived">已归档</SelectItem>
          </SelectContent>
        </Select>
        <Select value={sourceFilter} onValueChange={(v) => { setSourceFilter(v); setPage(1); load(statusFilter, v, 1) }}>
          <SelectTrigger className="w-32 h-8 text-xs">
            <SelectValue placeholder="来源筛选" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">全部来源</SelectItem>
            {Object.entries(WATCH_SOURCE_LABEL).map(([k, v]) => (
              <SelectItem key={k} value={k}>{v}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-xs text-ink-muted">共 {total} 条信号（技术热点周报 · 设计文档 §7.2.5）</span>
      </div>

      {error ? (
        <Card>
          <CardContent className="py-8 text-center text-xs text-state-archived">{error}</CardContent>
        </Card>
      ) : loading ? (
        <Card>
          <CardContent className="py-8 text-center text-xs text-ink-faint">加载观察池…</CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            {items.length === 0 ? (
              <p className="py-10 text-center text-xs text-ink-faint">暂无观察池信号（依赖每日观察池任务）</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>技能</TableHead>
                    <TableHead>信号源</TableHead>
                    <TableHead>信号值</TableHead>
                    <TableHead>周期</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>最近信号</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((w, i) => (
                    <TableRow key={`${w.skill_name}-${w.signal_source}-${w.period}-${i}`}>
                      <TableCell className="font-medium text-ink">{w.skill_name}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-[10px] font-mono">
                          {WATCH_SOURCE_LABEL[w.signal_source] ?? w.signal_source}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono tabular-nums text-ink-muted">
                        {typeof w.signal_value === 'number' ? w.signal_value.toFixed(3) : w.signal_value}
                      </TableCell>
                      <TableCell className="text-xs text-ink-muted font-mono">{w.period}</TableCell>
                      <TableCell>
                        <Badge
                          variant={w.status === 'candidate_promoted' ? 'emerging' : w.status === 'archived' ? 'archived' : 'outline'}
                          className="text-[10px]"
                        >
                          {w.status === 'candidate_promoted' ? '候选提升' : w.status === 'archived' ? '已归档' : '观察中'}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-ink-faint font-mono">
                        {w.last_signal_at ? w.last_signal_at.slice(0, 16).replace('T', ' ') : '—'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            {/* 翻页：总条数 > 每页 50 时出现（后端 /admin/discovery/watch 已分页） */}
            {total > PAGE_SIZE && (
              <div className="flex items-center justify-between border-t border-border px-4 py-2.5">
                <span className="text-xs text-ink-muted">
                  第 {page} / {totalPages} 页 · 每页 {PAGE_SIZE} 条
                </span>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 px-2.5 text-xs"
                    disabled={page <= 1 || loading}
                    onClick={() => { const p = page - 1; setPage(p); load(statusFilter, sourceFilter, p) }}
                  >
                    上一页
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 px-2.5 text-xs"
                    disabled={page >= totalPages || loading}
                    onClick={() => { const p = page + 1; setPage(p); load(statusFilter, sourceFilter, p) }}
                  >
                    下一页
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
