import { useEffect, useState } from 'react'
import { CheckCircle2, Clock, FileText, ShieldCheck, XCircle, TrendingUp, Archive } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
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
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { apiGet, apiPost, apiPut, ApiError } from '@/lib/api'

/** 后端 /admin/positions/pending 候选池项（AL-M4-01 DiscoveryCandidate） */
interface ReviewItem {
  id: string
  position_name: string
  state: string
  features: Record<string, unknown> | null
  confidence: Record<string, unknown> | null
  evidence_refs: string[]
  seed_matched: boolean
  rag_matched: boolean
  definition_draft: string
  detected_at: string
  updated_at: string | null
}

/** 综合置信度（后端 confidence 为多维对象，取 final_confidence 或 0.5 中性值） */
function confidenceOf(item: ReviewItem): number {
  const c = item.confidence
  if (c && typeof c === 'object') {
    const score = (c as Record<string, unknown>).final_confidence
    if (typeof score === 'number') return score
  }
  return 0.5
}

const CONFIDENCE_TONE = (c: number) => (c >= 0.8 ? 'text-state-emerging' : c >= 0.7 ? 'text-state-stable' : 'text-state-declining')

/** 后端 /admin/evolution/pending 项（emerging 待演化审核） */
interface EvolutionItem {
  id: string
  position_name: string
  state: string
  confidence: number | null
  evidence_refs: string[]
  seed_matched: boolean
  rag_matched: boolean
  definition_draft: string
  detected_at: string
  updated_at: string | null
}

/** 后端 /admin/positions/declining 项（declining 待归档） */
interface DecliningItem {
  id: string
  position_name: string
  state: string
  confidence: number | null
  evidence_refs: string[]
  detected_at: string
  updated_at: string | null
}

/**
 * 岗位审核页 — 设计文档 §7.2.2 + AL-M4-01
 *
 * 待审核队列来自真实 GET /admin/positions/pending；审核操作走真实
 * POST /admin/positions/{id}/review（approve → emerging / reject → rejected，
 * reason 必填，Neo4j 状态同步 + 审计日志由后端完成）。
 */
export function AdminReviewPage() {
  const [tab, setTab] = useState<'candidate' | 'evolution'>('candidate')
  const [queue, setQueue] = useState<ReviewItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  /* 审核操作 */
  const [reviewTarget, setReviewTarget] = useState<ReviewItem | null>(null)
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const loadQueue = () => {
    apiGet<{ items: ReviewItem[]; total: number }>('/admin/positions/pending')
      .then((res) => setQueue(res.items))
      .catch(() => setError('审核队列加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    // effect 内仅触发异步请求，setState 均在回调中（避免 cascading renders）
    loadQueue()
  }, [])

  async function submitReview(action: 'approve' | 'reject') {
    if (!reviewTarget) return
    if (!reason.trim()) {
      setNotice('请填写审核原因（reason 必填）')
      return
    }
    setSubmitting(true)
    setNotice(null)
    try {
      await apiPost(`/admin/positions/${reviewTarget.id}/review`, { action, reason: reason.trim() })
      setReviewTarget(null)
      setReason('')
      setNotice(`已${action === 'approve' ? '批准晋升 emerging' : '驳回（rejected）'}：${reviewTarget.position_name}`)
      loadQueue()
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : '审核提交失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <PageHeader
        title="岗位审核"
        description="六状态机全链路人工审核：候选晋升（candidate → emerging / rejected）· 演化晋级（emerging → stable / declining）· 衰退归档（declining → archived）"
      />
      <Tabs value={tab} onValueChange={(v) => setTab(v as 'candidate' | 'evolution')}>
        <TabsList>
          <TabsTrigger value="candidate" className="text-xs">候选晋升审核</TabsTrigger>
          <TabsTrigger value="evolution" className="text-xs">演化审核（emerging）</TabsTrigger>
        </TabsList>
        <TabsContent value="candidate">
      {/* 审核结果通知 */}
      {notice && (
        <div className="mb-4 rounded-md border border-state-candidate/20 bg-state-candidate/5 px-4 py-3 text-sm text-state-candidate">
          {notice}
        </div>
      )}

      {/* 统计卡（真实：待审核=队列长度；状态机分布由后端候选池状态聚合） */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <Clock className="size-4 text-ink-faint" />
              <Badge variant="candidate">candidate</Badge>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">{queue.length}</div>
            <div className="text-xs text-ink-muted mt-1">待审核（真实）</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <CheckCircle2 className="size-4 text-ink-faint" />
              <span className="text-xs font-mono text-ink-muted">RAG</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">
              {queue.filter((q) => q.rag_matched).length}
            </div>
            <div className="text-xs text-ink-muted mt-1">RAG 权威库命中</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <FileText className="size-4 text-ink-faint" />
              <span className="text-xs font-mono text-ink-muted">SEED</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">
              {queue.filter((q) => q.seed_matched).length}
            </div>
            <div className="text-xs text-ink-muted mt-1">种子列表命中</div>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 审核队列（真实 pending） */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-sm flex items-center justify-between">
              <span>审核队列</span>
              <span className="text-xs font-normal text-ink-faint">{queue.length} 条待处理</span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <p className="py-12 text-center text-sm text-ink-muted">加载审核队列…</p>
            ) : error ? (
              <p className="py-12 text-center text-sm text-state-archived">{error}</p>
            ) : queue.length === 0 ? (
              <div className="py-12 text-center text-sm text-ink-faint">
                <p>暂无待审核岗位</p>
                <p className="text-xs mt-2">
                  新岗位候选由「JD 抽取 + 发现检测器（Z-score/Wilson 门控）+ RAG 接地」产出，
                  冷启动阶段候选池可能为空（属预期，见设计文档 7.2）
                </p>
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>岗位名</TableHead>
                    <TableHead>命中信号</TableHead>
                    <TableHead>发现时间</TableHead>
                    <TableHead>置信度</TableHead>
                    <TableHead className="text-right">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {queue.map((item) => (
                    <TableRow key={item.id}>
                      <TableCell className="font-medium max-w-48 truncate">{item.position_name}</TableCell>
                      <TableCell>
                        <div className="flex gap-1">
                          {item.rag_matched && <Badge variant="outline" className="text-[10px]">RAG</Badge>}
                          {item.seed_matched && <Badge variant="outline" className="text-[10px]">种子</Badge>}
                          {!item.rag_matched && !item.seed_matched && (
                            <span className="text-[10px] text-ink-faint">—</span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-xs font-mono text-ink-muted">{item.detected_at}</TableCell>
                      <TableCell>
                        <span className={`font-mono tabular-nums text-sm ${CONFIDENCE_TONE(confidenceOf(item))}`}>
                          {Math.round(confidenceOf(item) * 100)}%
                        </span>
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            setReviewTarget(item)
                            setReason('')
                            setNotice(null)
                          }}
                        >
                          审核
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {/* 说明看板 */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm flex items-center gap-1.5">
                <ShieldCheck className="size-4" />
                状态机流转
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ol className="space-y-2 text-xs text-ink-secondary">
                <li className="flex items-center gap-2">
                  <Badge variant="candidate">candidate</Badge>
                  <span>→</span>
                  <Badge variant="emerging">emerging</Badge>
                  <span className="text-ink-faint">conf≥0.6 且源≥2</span>
                </li>
                <li className="flex items-center gap-2">
                  <Badge variant="candidate">candidate</Badge>
                  <span>→</span>
                  <Badge variant="archived">rejected</Badge>
                  <span className="text-ink-faint">人工驳回</span>
                </li>
                <li className="pt-2 text-[11px] text-ink-faint">
                  审核写 AuditLog（reason 必填），Neo4j Position.status 同步由后端完成
                </li>
              </ol>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">岗位定义草案</CardTitle>
            </CardHeader>
            <CardContent>
              {reviewTarget ? (
                <p className="max-h-40 overflow-auto text-xs text-ink-secondary leading-relaxed">
                  {reviewTarget.definition_draft || '（无定义草案）'}
                </p>
              ) : (
                <p className="text-xs text-ink-faint py-6 text-center border border-dashed border-border rounded-md">
                  选中队列中的岗位后展示其定义草案与审核操作
                </p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* 审核确认 Dialog（POST /admin/positions/{id}/review） */}
      <Dialog open={reviewTarget !== null} onOpenChange={(o) => !o && setReviewTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>审核岗位：{reviewTarget?.position_name}</DialogTitle>
            <DialogDescription>
              批准将晋升为 emerging（需置信度 ≥0.6 且 ≥2 个证据源）；驳回则标记 rejected。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="rounded-md bg-subtle p-3 text-xs text-ink-secondary">
              <div className="mb-1 font-medium text-ink">定义草案</div>
              <p className="leading-relaxed">{reviewTarget?.definition_draft || '（无定义草案）'}</p>
              {reviewTarget && reviewTarget.evidence_refs.length > 0 && (
                <div className="mt-2">
                  <div className="mb-0.5 font-medium text-ink">证据引用</div>
                  <p className="font-mono text-[10px] text-ink-faint break-all">
                    {reviewTarget.evidence_refs.join('、')}
                  </p>
                </div>
              )}
            </div>
            <label className="block space-y-1.5">
              <span className="text-xs text-ink-muted">审核原因 *</span>
              <Textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={3}
                placeholder="填写批准或驳回的原因（必填）"
              />
            </label>
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setReviewTarget(null)}>
                取消
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="text-state-archived"
                disabled={submitting}
                onClick={() => submitReview('reject')}
              >
                <XCircle className="size-3.5 mr-1" />
                驳回
              </Button>
              <Button size="sm" disabled={submitting} onClick={() => submitReview('approve')}>
                <CheckCircle2 className="size-3.5 mr-1" />
                批准晋升
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
        </TabsContent>
        <TabsContent value="evolution">
          <EvolutionReviewTab />
        </TabsContent>
      </Tabs>
    </>
  )
}

/**
 * 演化审核 Tab — 设计文档 §7.3 + M4
 *
 * 待审队列来自真实 GET /admin/evolution/pending（emerging 状态岗位）；
 * 审核操作走 PUT /admin/evolution/{id}/review：
 * - approve → 确认晋级 stable（六状态机 Neo4j 同步）
 * - reject  → 确认衰退 declining
 * reason 可选（后端默认写入 "admin evolution review"）。
 */
function EvolutionReviewTab() {
  const [items, setItems] = useState<EvolutionItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  /* 审核操作 */
  const [reviewTarget, setReviewTarget] = useState<EvolutionItem | null>(null)
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  /* 衰退归档（declining → archived 终态） */
  const [decliningItems, setDecliningItems] = useState<DecliningItem[]>([])
  const [decliningLoading, setDecliningLoading] = useState(true)
  const [archiveTarget, setArchiveTarget] = useState<DecliningItem | null>(null)
  const [archiveReason, setArchiveReason] = useState('')
  const [archiving, setArchiving] = useState(false)

  const load = () => {
    apiGet<{ items: EvolutionItem[]; total: number }>('/admin/evolution/pending')
      .then((res) => setItems(res.items))
      .catch(() => setError('演化审核队列加载失败'))
      .finally(() => setLoading(false))
  }

  const loadDeclining = () => {
    apiGet<{ items: DecliningItem[]; total: number }>('/admin/positions/declining')
      .then((res) => setDecliningItems(res.items))
      .catch(() => setDecliningItems([]))
      .finally(() => setDecliningLoading(false))
  }

  useEffect(() => {
    load()
    loadDeclining()
  }, [])

  async function submitReview(action: 'approve' | 'reject') {
    if (!reviewTarget) return
    setSubmitting(true)
    setNotice(null)
    try {
      await apiPut(`/admin/evolution/${reviewTarget.id}/review`, { action, reason: reason.trim() })
      setReviewTarget(null)
      setReason('')
      setNotice(`已${action === 'approve' ? '确认晋级 stable' : '确认衰退 declining'}：${reviewTarget.position_name}`)
      load()
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : '审核提交失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  // 确认衰退归档（PUT /admin/positions/{id}/archive，declining → archived 终态）
  async function submitArchive() {
    if (!archiveTarget) return
    if (!archiveReason.trim()) {
      setNotice('归档必须填写 reason')
      return
    }
    setArchiving(true)
    setNotice(null)
    try {
      await apiPut(`/admin/positions/${archiveTarget.id}/archive`, { reason: archiveReason.trim() })
      setArchiveTarget(null)
      setArchiveReason('')
      setNotice(`已归档（终态）：${archiveTarget.position_name}`)
      loadDeclining()
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : '归档提交失败，请重试')
    } finally {
      setArchiving(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* 审核结果通知 */}
      {notice && (
        <div className="rounded-md border border-state-emerging/20 bg-state-emerging/5 px-4 py-3 text-sm text-state-emerging">
          {notice}
        </div>
      )}

      {/* 统计卡（真实：待审核=队列长度；approve/reject 数由状态机分布聚合） */}
      <div className="grid grid-cols-3 gap-4">
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <Clock className="size-4 text-ink-faint" />
              <Badge variant="emerging">emerging</Badge>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">{items.length}</div>
            <div className="text-xs text-ink-muted mt-1">待演化审核（真实）</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <TrendingUp className="size-4 text-ink-faint" />
              <Badge variant="outline" className="text-[10px]">approve</Badge>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">
              {items.filter((i) => i.confidence != null && i.confidence >= 0.6).length}
            </div>
            <div className="text-xs text-ink-muted mt-1">置信度 ≥0.6 建议晋级</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <FileText className="size-4 text-ink-faint" />
              <span className="text-xs font-mono text-ink-muted">RAG</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">
              {items.filter((i) => i.rag_matched).length}
            </div>
            <div className="text-xs text-ink-muted mt-1">RAG 权威库命中</div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center justify-between">
            <span>演化审核队列</span>
            <span className="text-xs font-normal text-ink-faint">{items.length} 条待处理</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="py-12 text-center text-sm text-ink-muted">加载演化审核队列…</p>
          ) : error ? (
            <p className="py-12 text-center text-sm text-state-archived">{error}</p>
          ) : items.length === 0 ? (
            <div className="py-12 text-center text-sm text-ink-faint">
              <p>暂无待演化审核的 emerging 岗位</p>
              <p className="text-xs mt-2">
                已晋升 emerging 的岗位需人工确认：晋级 stable（收入稳定画像）或判定衰退 declining
              </p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>岗位名</TableHead>
                  <TableHead>命中信号</TableHead>
                  <TableHead>发现时间</TableHead>
                  <TableHead>置信度</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="font-medium max-w-48 truncate">{item.position_name}</TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        {item.rag_matched && <Badge variant="outline" className="text-[10px]">RAG</Badge>}
                        {item.seed_matched && <Badge variant="outline" className="text-[10px]">种子</Badge>}
                        {!item.rag_matched && !item.seed_matched && (
                          <span className="text-[10px] text-ink-faint">—</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs font-mono text-ink-muted">{item.detected_at}</TableCell>
                    <TableCell>
                      {item.confidence != null ? (
                        <span className={`font-mono tabular-nums text-sm ${CONFIDENCE_TONE(item.confidence)}`}>
                          {Math.round(item.confidence * 100)}%
                        </span>
                      ) : (
                        <span className="text-xs text-ink-faint">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setReviewTarget(item)
                          setReason('')
                          setNotice(null)
                        }}
                      >
                        审核
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* 演化审核确认 Dialog（PUT /admin/evolution/{id}/review） */}
      <Dialog open={reviewTarget !== null} onOpenChange={(o) => !o && setReviewTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>演化审核：{reviewTarget?.position_name}</DialogTitle>
            <DialogDescription>
              确认晋级 stable（收入稳定岗位画像）或判定衰退 declining（六状态机 Neo4j 同步由后端完成）。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            {reviewTarget && (
              <div className="rounded-md bg-subtle p-3 text-xs text-ink-secondary">
                <div className="mb-1 font-medium text-ink">岗位定义草案</div>
                <p className="leading-relaxed">{reviewTarget.definition_draft || '（无定义草案）'}</p>
                {reviewTarget.evidence_refs.length > 0 && (
                  <div className="mt-2">
                    <div className="mb-0.5 font-medium text-ink">证据引用</div>
                    <p className="font-mono text-[10px] text-ink-faint break-all">
                      {reviewTarget.evidence_refs.join('、')}
                    </p>
                  </div>
                )}
              </div>
            )}
            <label className="block space-y-1.5">
              <span className="text-xs text-ink-muted">审核说明（可选）</span>
              <Textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                placeholder="填写晋级/衰退的原因（可留空）"
              />
            </label>
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setReviewTarget(null)}>
                取消
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="text-state-declining"
                disabled={submitting}
                onClick={() => submitReview('reject')}
              >
                <XCircle className="size-3.5 mr-1" />
                确认衰退
              </Button>
              <Button size="sm" disabled={submitting} onClick={() => submitReview('approve')}>
                <CheckCircle2 className="size-3.5 mr-1" />
                确认晋级 stable
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* 衰退归档（declining → archived 终态，真实 GET /admin/positions/declining） */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center justify-between">
            <span className="flex items-center gap-2">
              <Archive className="size-4" />
              衰退归档（declining）
            </span>
            <span className="text-xs font-normal text-ink-faint">{decliningItems.length} 条待归档</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {decliningLoading ? (
            <p className="py-8 text-center text-sm text-ink-muted">加载衰退岗位…</p>
          ) : decliningItems.length === 0 ? (
            <div className="py-8 text-center text-sm text-ink-faint">
              <p>暂无待归档的 declining 岗位</p>
              <p className="text-xs mt-2">
                连续 3 窗口频次下降 &gt;40% 自动进入 declining，需人工确认归档（终态）
              </p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>岗位名</TableHead>
                  <TableHead>发现时间</TableHead>
                  <TableHead>置信度</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {decliningItems.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="font-medium max-w-48 truncate">{item.position_name}</TableCell>
                    <TableCell className="text-xs font-mono text-ink-muted">{item.detected_at}</TableCell>
                    <TableCell>
                      {item.confidence != null ? (
                        <span className={`font-mono tabular-nums text-sm ${CONFIDENCE_TONE(item.confidence)}`}>
                          {Math.round(item.confidence * 100)}%
                        </span>
                      ) : (
                        <span className="text-xs text-ink-faint">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-state-archived"
                        onClick={() => {
                          setArchiveTarget(item)
                          setArchiveReason('')
                          setNotice(null)
                        }}
                      >
                        <Archive className="size-3.5 mr-1" />
                        确认归档
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* 归档确认 Dialog（PUT /admin/positions/{id}/archive） */}
      <Dialog open={archiveTarget !== null} onOpenChange={(o) => !o && setArchiveTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认衰退归档：{archiveTarget?.position_name}</DialogTitle>
            <DialogDescription>
              归档为终态（archived），岗位将从活跃画像移除。Neo4j Position.status 同步与审计记录由后端完成。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            {archiveTarget && (
              <div className="rounded-md bg-subtle p-3 text-xs text-ink-secondary">
                <span className="text-ink-faint">证据引用：</span>
                <span className="font-mono text-[10px] text-ink-faint break-all">
                  {archiveTarget.evidence_refs.length > 0 ? archiveTarget.evidence_refs.join('、') : '—'}
                </span>
              </div>
            )}
            <label className="block space-y-1.5">
              <span className="text-xs text-ink-muted">归档原因（必填）</span>
              <Textarea
                value={archiveReason}
                onChange={(e) => setArchiveReason(e.target.value)}
                rows={2}
                placeholder="填写归档原因（状态机强制要求，写入审计日志）"
              />
            </label>
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setArchiveTarget(null)}>
                取消
              </Button>
              <Button
                size="sm"
                className="bg-state-archived hover:bg-state-archived/90"
                disabled={archiving}
                onClick={submitArchive}
              >
                <Archive className="size-3.5 mr-1" />
                确认归档（终态）
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
