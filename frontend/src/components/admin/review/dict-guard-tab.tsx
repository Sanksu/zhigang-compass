/**
 * 字典守卫 Tab — 技能字典自治守卫方案 §7（PR-C/D）
 *
 * 数据源：GET /admin/dict-guard/proposals（LLM 评估提案，默认 pending）、
 * GET /admin/dict-guard/changes（动态层变更审计）、
 * GET /admin/dict-guard/report/latest（最近巡检报告）。
 * 操作：POST proposals/{id}/review（approve 执行动态层变更 / reject，reason 必填）、
 * POST changes/{id}/rollback（反向操作，后端防复滚）。
 */
import { useEffect, useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { apiGet, apiPost, errMsg } from '@/lib/api'
import type { Schema } from './review-types'

type Proposal = Schema['DictProposalItem']
type ChangeLog = Schema['DictChangeLogItem']

const ACTION_LABEL: Record<string, string> = {
  add_stopword: '加入停用词',
  remove_stopword: '移除停用词',
  protect_whitelist: '加白保护',
  remove_node: '删除节点',
  remove_edge: '删除脏边',
}

const ENTITY_LABEL: Record<string, string> = {
  skill: '技能',
  position: '岗位',
  course: '课程',
}

/** 提案证据里的受影响技能（静态停用词 remove 审批的落地目标） */
function victimOf(evidence: unknown): string {
  if (!Array.isArray(evidence)) return ''
  for (const e of evidence) {
    if (e && typeof e === 'object' && (e as Record<string, unknown>).label === '受影响技能') {
      return String((e as Record<string, unknown>).value ?? '')
    }
  }
  return ''
}

interface ReportSummary {
  run_date?: string
  candidates?: number
  evaluated?: number
  llm_failed?: number
  auto_applied?: { term: string; reason: string }[]
  proposals?: number
}

export function DictGuardTab() {
  /* 待审提案 */
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [proposalTotal, setProposalTotal] = useState(0)
  const [proposalPage, setProposalPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState<'pending' | 'approved' | 'rejected'>('pending')
  /* 变更审计 */
  const [changes, setChanges] = useState<ChangeLog[]>([])
  const [changeTotal, setChangeTotal] = useState(0)
  const [changePage, setChangePage] = useState(1)
  /* 巡检报告 */
  const [report, setReport] = useState<ReportSummary | null>(null)

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  /* 审核弹窗 */
  const [reviewTarget, setReviewTarget] = useState<Proposal | null>(null)
  const [reviewAction, setReviewAction] = useState<'approve' | 'reject'>('approve')
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [triggering, setTriggering] = useState(false)

  const PAGE_SIZE = 20

  const loadProposals = (status = statusFilter, p = proposalPage) => {
    const params = new URLSearchParams({ page: String(p), size: String(PAGE_SIZE), status })
    return apiGet<{ items: Proposal[]; total: number }>(`/admin/dict-guard/proposals?${params}`)
      .then((res) => {
        setProposals(res.items)
        setProposalTotal(res.total)
      })
      .catch(() => setError('提案加载失败'))
  }
  const loadChanges = (p = changePage) => {
    const params = new URLSearchParams({ page: String(p), size: String(PAGE_SIZE) })
    return apiGet<{ items: ChangeLog[]; total: number }>(`/admin/dict-guard/changes?${params}`)
      .then((res) => {
        setChanges(res.items)
        setChangeTotal(res.total)
      })
      .catch(() => setError('变更历史加载失败'))
  }
  const loadReport = () =>
    apiGet<ReportSummary>('/admin/dict-guard/report/latest')
      .then(setReport)
      .catch(() => setReport(null))

  useEffect(() => {
    Promise.all([loadProposals(), loadChanges(), loadReport()]).finally(
      () => setLoading(false),
    )
    // effect 内仅触发异步请求，setState 均在回调中（避免 cascading renders）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function submitReview() {
    if (!reviewTarget) return
    if (!reason.trim()) {
      setNotice('请填写审核理由（reason 必填）')
      return
    }
    setSubmitting(true)
    setNotice(null)
    try {
      await apiPost(`/admin/dict-guard/proposals/${reviewTarget.id}/review`, {
        action: reviewAction,
        reason: reason.trim(),
      })
      setNotice(
        `已${reviewAction === 'approve' ? '批准执行' : '驳回'}：${ACTION_LABEL[reviewTarget.action] ?? reviewTarget.action}「${reviewTarget.term}」`,
      )
      setReviewTarget(null)
      setReason('')
      await Promise.all([loadProposals(statusFilter, 1), loadChanges(), loadReport()])
    } catch (e) {
      setNotice(errMsg(e, '审核提交失败，请重试'))
    } finally {
      setSubmitting(false)
    }
  }

  async function rollback(c: ChangeLog) {
    if (!window.confirm(`确认回滚变更「${c.term}」（${c.source}/${c.kind}）？将反向操作动态过滤层`)) return
    try {
      await apiPost(`/admin/dict-guard/changes/${c.id}/rollback`, {})
      setNotice(`已回滚：${c.term}`)
      await Promise.all([loadChanges(), loadReport()])
    } catch (e) {
      setNotice(errMsg(e, '回滚失败，请重试'))
    }
  }

  async function manualTrigger() {
    setTriggering(true)
    setNotice(null)
    try {
      await apiPost('/admin/dict-guard/trigger', {})
      setNotice('字典守卫巡检已提交，等待 worker 执行；稍后可查看报告')
      setReport(null) // 触发后清空旧摘要，提示即将重跑
      loadReport()
    } catch (e) {
      setNotice(errMsg(e, '触发失败，请重试'))
    } finally {
      setTriggering(false)
    }
  }

  const totalPages = Math.max(1, Math.ceil(proposalTotal / PAGE_SIZE))
  const changeTotalPages = Math.max(1, Math.ceil(changeTotal / PAGE_SIZE))

  return (
    <div className="space-y-4">
      {notice && (
        <div className="rounded-md border border-state-candidate/20 bg-state-candidate/5 px-4 py-3 text-sm text-state-candidate">
          {notice}
        </div>
      )}

      {/* 最近巡检报告摘要 + 手动触发 */}
      <Card>
        <CardContent className="py-3 px-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-xs text-ink-muted">
              {report ? (
                <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
                  <span className="font-medium text-ink">最近巡检 {report.run_date}</span>
                  <span>候选 <b className="font-mono text-ink">{report.candidates ?? 0}</b></span>
                  <span>已评估 <b className="font-mono text-ink">{report.evaluated ?? 0}</b></span>
                  <span>LLM 失败 <b className="font-mono text-ink">{report.llm_failed ?? 0}</b></span>
                  <span>自动生效 <b className="font-mono text-ink">{report.auto_applied?.length ?? 0}</b></span>
                  <span>转人工 <b className="font-mono text-ink">{report.proposals ?? 0}</b></span>
                </div>
              ) : (
                <p>暂无巡检报告（依赖每日 ETL 阶段 16 字典守卫任务）</p>
              )}
            </div>
            <Button
              size="sm"
              variant="outline"
              className="h-8 text-xs"
              disabled={triggering}
              onClick={manualTrigger}
            >
              {triggering ? '提交中…' : '手动巡检'}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 待审提案 */}
      <Card>
        <CardContent className="p-0">
          <div className="flex flex-wrap items-center justify-between gap-3 px-4 pt-4">
            <h3 className="text-sm font-medium text-ink">待审提案</h3>
            <Select
              value={statusFilter}
              onValueChange={(v) => {
                setStatusFilter(v as typeof statusFilter)
                setProposalPage(1)
                loadProposals(v as typeof statusFilter, 1)
              }}
            >
              <SelectTrigger className="w-32 h-8 text-xs">
                <SelectValue placeholder="状态筛选" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="pending">待审</SelectItem>
                <SelectItem value="approved">已批准</SelectItem>
                <SelectItem value="rejected">已驳回</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {error ? (
            <p className="py-8 text-center text-xs text-state-archived">{error}</p>
          ) : loading ? (
            <p className="py-8 text-center text-xs text-ink-faint">加载提案…</p>
          ) : proposals.length === 0 ? (
            <p className="py-10 text-center text-xs text-ink-faint">
              暂无{statusFilter === 'pending' ? '待审' : ''}提案（remove/protect 及超阈值调整会进入此处人工审批）
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>词条</TableHead>
                  <TableHead>对象</TableHead>
                  <TableHead>动作</TableHead>
                  <TableHead>理由</TableHead>
                  <TableHead>置信度</TableHead>
                  <TableHead>影响面</TableHead>
                  <TableHead>批次</TableHead>
                  {statusFilter === 'pending' && <TableHead className="text-right">操作</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {proposals.map((pr) => {
                  const victim = victimOf(pr.evidence)
                  return (
                    <TableRow key={pr.id}>
                      <TableCell className="font-medium text-ink">{pr.term}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-[10px] font-mono">
                          {ENTITY_LABEL[pr.entity_type ?? 'skill'] ?? pr.entity_type ?? 'skill'}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-[10px]">
                          {ACTION_LABEL[pr.action] ?? pr.action}
                        </Badge>
                      </TableCell>
                      <TableCell className="max-w-[240px] text-xs text-ink-muted">
                        {pr.reason || '—'}
                        {victim && (
                          <span className="ml-1 text-state-declining">（误杀：{victim}）</span>
                        )}
                      </TableCell>
                      <TableCell className="font-mono tabular-nums text-xs text-ink-muted">
                        {typeof pr.llm_confidence === 'number' ? pr.llm_confidence.toFixed(2) : '—'}
                      </TableCell>
                      <TableCell className="font-mono tabular-nums text-xs text-ink-muted" title="图谱同名节点 / 命中 JD">
                        图 {(pr.impact_stats as Record<string, number>)?.graph_nodes ?? 0} · JD{' '}
                        {(pr.impact_stats as Record<string, number>)?.jd_snapshots ?? 0}
                      </TableCell>
                      <TableCell className="text-xs text-ink-faint font-mono">{pr.run_date}</TableCell>
                      {statusFilter === 'pending' && (
                        <TableCell className="text-right">
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 px-2 text-xs"
                            onClick={() => {
                              setReviewTarget(pr)
                              setReviewAction('approve')
                              setReason('')
                            }}
                          >
                            通过
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="ml-1 h-7 px-2 text-xs text-state-archived hover:text-state-archived"
                            onClick={() => {
                              setReviewTarget(pr)
                              setReviewAction('reject')
                              setReason('')
                            }}
                          >
                            驳回
                          </Button>
                        </TableCell>
                      )}
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
          {proposalTotal > PAGE_SIZE && (
            <div className="flex items-center justify-between border-t border-border px-4 py-2.5">
              <span className="text-xs text-ink-muted">
                第 {proposalPage} / {totalPages} 页 · 共 {proposalTotal} 条
              </span>
              <div className="flex items-center gap-2">
                <Button
                  size="sm" variant="outline" className="h-7 px-2.5 text-xs"
                  disabled={proposalPage <= 1 || loading}
                  onClick={() => { const p = proposalPage - 1; setProposalPage(p); loadProposals(statusFilter, p) }}
                >
                  上一页
                </Button>
                <Button
                  size="sm" variant="outline" className="h-7 px-2.5 text-xs"
                  disabled={proposalPage >= totalPages || loading}
                  onClick={() => { const p = proposalPage + 1; setProposalPage(p); loadProposals(statusFilter, p) }}
                >
                  下一页
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 变更审计 */}
      <Card>
        <CardContent className="p-0">
          <h3 className="px-4 pt-4 text-sm font-medium text-ink">动态过滤层变更审计</h3>
          {changes.length === 0 ? (
            <p className="py-8 text-center text-xs text-ink-faint">暂无变更记录</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>词条</TableHead>
                  <TableHead>动作</TableHead>
                  <TableHead>来源</TableHead>
                  <TableHead>类型</TableHead>
                  <TableHead>理由</TableHead>
                  <TableHead>操作人</TableHead>
                  <TableHead>时间</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {changes.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="font-medium text-ink">{c.term}</TableCell>
                    <TableCell className="text-xs">{ACTION_LABEL[c.action] ?? c.action}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-[10px] font-mono">{c.source}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={c.kind === 'blocked' ? 'archived' : 'emerging'} className="text-[10px]">
                        {c.kind === 'blocked'
                          ? '拦截'
                          : c.kind === 'protected'
                            ? '保护'
                            : c.kind === 'node'
                              ? '节点'
                              : c.kind === 'edge'
                                ? '脏边'
                                : c.kind}
                      </Badge>
                    </TableCell>
                    <TableCell className="max-w-[200px] truncate text-xs text-ink-muted">{c.reason || '—'}</TableCell>
                    <TableCell className="text-xs text-ink-muted font-mono">{c.applied_by}</TableCell>
                    <TableCell className="text-xs text-ink-faint font-mono">
                      {c.created_at ? String(c.created_at).slice(0, 16).replace('T', ' ') : '—'}
                    </TableCell>
                    <TableCell className="text-right">
                      {c.action !== 'rollback' && c.kind !== 'node' && c.kind !== 'edge' && (
                        <Button
                          size="sm" variant="ghost" className="h-7 px-2 text-xs text-ink-muted hover:text-ink"
                          onClick={() => rollback(c)}
                        >
                          回滚
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {changeTotal > PAGE_SIZE && (
            <div className="flex items-center justify-between border-t border-border px-4 py-2.5">
              <span className="text-xs text-ink-muted">
                第 {changePage} / {changeTotalPages} 页 · 共 {changeTotal} 条
              </span>
              <div className="flex items-center gap-2">
                <Button
                  size="sm" variant="outline" className="h-7 px-2.5 text-xs"
                  disabled={changePage <= 1 || loading}
                  onClick={() => { const p = changePage - 1; setChangePage(p); loadChanges(p) }}
                >
                  上一页
                </Button>
                <Button
                  size="sm" variant="outline" className="h-7 px-2.5 text-xs"
                  disabled={changePage >= changeTotalPages || loading}
                  onClick={() => { const p = changePage + 1; setChangePage(p); loadChanges(p) }}
                >
                  下一页
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 审核弹窗（reason 必填） */}
      <Dialog open={!!reviewTarget} onOpenChange={(open) => !open && setReviewTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {reviewAction === 'approve' ? '通过执行' : '驳回提案'}：
              {reviewTarget ? `${ACTION_LABEL[reviewTarget.action] ?? reviewTarget.action}「${reviewTarget.term}」` : ''}
            </DialogTitle>
            <DialogDescription>
              {reviewAction === 'approve'
                ? '批准后立即写入动态过滤层并记入变更审计（可回滚）'
                : '驳回仅记录结论，不产生任何过滤层变更'}
            </DialogDescription>
          </DialogHeader>
          <Textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="审核理由（必填）"
            rows={3}
          />
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={() => setReviewTarget(null)}>
              取消
            </Button>
            <Button size="sm" className="h-8 text-xs" disabled={submitting} onClick={submitReview}>
              {submitting ? '提交中…' : '确认提交'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
