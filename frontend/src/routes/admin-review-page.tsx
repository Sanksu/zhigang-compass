import { useEffect, useState } from 'react'
import { CheckCircle2, Clock, FileText } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { apiGet } from '@/lib/api'

interface ReviewItem {
  id: string
  name: string
  platform: string
  foundAt: string
  confidence: number
  jdCount: number
  description: string
  skills: string[]
}

const CONFIDENCE_TONE = (c: number) => (c >= 0.8 ? 'text-state-emerging' : c >= 0.7 ? 'text-state-stable' : 'text-state-declining')

/**
 * 岗位审核页 — 设计文档 §7.2.2
 *
 * 待审核队列来自真实 GET /admin/positions/pending。当前 LLM 抽取 + 发现检测器
 * 尚未接入真实数据（M3/M4 交付），队列为空并显示说明。
 */
export function AdminReviewPage() {
  const [queue, setQueue] = useState<ReviewItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiGet<{ items: ReviewItem[]; total: number }>('/admin/positions/pending')
      .then((res) => setQueue(res.items))
      .catch(() => setError('审核队列加载失败'))
      .finally(() => setLoading(false))
  }, [])

  return (
    <>
      <PageHeader title="岗位审核" description="candidate → emerging / rejected · 新兴岗位审批队列" />

      {/* 统计卡（真实：待审核=队列长度；今日/本周审核需后端记录端点，暂为 0） */}
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
              <span className="text-xs font-mono text-ink-muted">—</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">0</div>
            <div className="text-xs text-ink-muted mt-1">今日已审核</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <FileText className="size-4 text-ink-faint" />
              <span className="text-xs font-mono text-ink-muted">—</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">0</div>
            <div className="text-xs text-ink-muted mt-1">本周已审核</div>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 审核队列（真实 pending，当前为空） */}
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
                  新岗位候选由「JD 抽取 + 发现检测器（Z-score/Wilson 门控）」产出，
                  等待 LLM 抽取上线后接入（M3/M4）
                </p>
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>岗位名</TableHead>
                    <TableHead>来源平台</TableHead>
                    <TableHead>发现时间</TableHead>
                    <TableHead>置信度</TableHead>
                    <TableHead className="text-right">JD 数</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {queue.map((item) => (
                    <TableRow key={item.id}>
                      <TableCell className="font-medium">{item.name}</TableCell>
                      <TableCell className="text-ink-secondary">{item.platform}</TableCell>
                      <TableCell className="text-xs font-mono text-ink-muted">{item.foundAt}</TableCell>
                      <TableCell>
                        <span className={`font-mono tabular-nums text-sm ${CONFIDENCE_TONE(item.confidence)}`}>
                          {Math.round(item.confidence * 100)}%
                        </span>
                      </TableCell>
                      <TableCell className="text-right tabular-nums font-mono">{item.jdCount}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {/* 状态机看板 + 审核日志（后端待交付 → 空态） */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">状态机分布</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-ink-faint py-6 text-center border border-dashed border-border rounded-md">
                六状态机分布由岗位生命周期管理产出，等待后端交付（M4）
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm flex items-center justify-between">
                <span>审核操作日志</span>
                <span className="text-xs font-normal text-ink-faint">最近 5 条</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-ink-faint py-6 text-center border border-dashed border-border rounded-md">
                审核操作将写入 audit_logs，等待审核流程实装后展示
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  )
}
