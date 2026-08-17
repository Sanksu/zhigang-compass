import { useEffect, useState } from 'react'
import {
  CheckCircle2,
  Clock,
  FileText,
  ShieldCheck,
  XCircle,
} from 'lucide-react'
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
import { apiGet, apiPost, errMsg } from '@/lib/api'
import {
  confidenceOf,
  CONFIDENCE_TONE,
  type Schema,
  type ReviewItem,
} from './review-types'

/**
 * 候选晋升审核 Tab — 设计文档 §7.2.2 + AL-M4-01
 *
 * 待审核队列来自真实 GET /admin/positions/pending；审核操作走真实
 * POST /admin/positions/{id}/review（approve → emerging / reject → rejected，
 * reason 必填，Neo4j 状态同步 + 审计日志由后端完成）。
 */
export function CandidateReviewTab() {
  const [queue, setQueue] = useState<ReviewItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  /* 审核操作 */
  const [reviewTarget, setReviewTarget] = useState<ReviewItem | null>(null)
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const loadQueue = () => {
    apiGet<Schema['DiscoveryCandidateData']>('/admin/positions/pending')
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
      setNotice(errMsg(e, '审核提交失败，请重试'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
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
    </>
  )
}
