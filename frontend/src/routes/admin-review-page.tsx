import { useState } from 'react'
import { CheckCircle2, Clock, FileText } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

/** 岗位六状态机 — 对齐设计文档 §7.2.2 */
type PositionState = 'candidate' | 'emerging' | 'stable' | 'declining' | 'archived' | 'rejected'

interface EvidenceItem {
  source: string
  snippet: string
  time: string
}

interface ReviewItem {
  id: string
  name: string
  platform: string
  foundAt: string
  confidence: number
  jdCount: number
  description: string
  skills: string[]
  evidence: EvidenceItem[]
  breakdown: { label: string; value: number }[]
}

interface AuditLog {
  id: string
  admin: string
  action: string
  position: string
  time: string
}

interface StateBucket {
  key: PositionState
  label: string
  count: number
}

/** 待审核队列 — 8 条 candidate 岗位，覆盖国内外主流平台 */
const MOCK_QUEUE: ReviewItem[] = [
  {
    id: 'r1',
    name: '大模型应用工程师',
    platform: 'BOSS直聘',
    foundAt: '07-29 14:20',
    confidence: 0.86,
    jdCount: 18,
    description: '负责基于大语言模型的产品功能设计与落地，需将 LLM 能力编排进业务流程，关注 RAG、Function Calling 与 Agent 工作流工程化。',
    skills: ['LangChain', 'RAG', 'Python', 'Prompt Engineering', 'Function Calling'],
    evidence: [
      { source: 'BOSS直聘 JD', snippet: '负责 LLM 应用层开发，具备 RAG 与 Agent 编排经验', time: '07-29 14:18' },
      { source: 'GitHub 仓库', snippet: '近 30 天 248 个 langchain-agent 项目新增', time: '07-29 14:15' },
      { source: 'Stack Overflow', snippet: 'llm 相关提问周环比 +32%', time: '07-29 14:10' },
    ],
    breakdown: [
      { label: '技能覆盖度', value: 0.88 },
      { label: '出现频次', value: 0.82 },
      { label: '时间新近度', value: 0.9 },
    ],
  },
  {
    id: 'r2',
    name: 'AI Agent 开发工程师',
    platform: 'GitHub',
    foundAt: '07-29 13:50',
    confidence: 0.78,
    jdCount: 12,
    description: '设计并实现具备规划、记忆、工具调用能力的智能体系统，强调多 Agent 协作与工具链集成。',
    skills: ['LangGraph', 'OpenAI API', 'Vector DB', 'Python'],
    evidence: [
      { source: 'GitHub 仓库', snippet: 'agent 相关 repo 周增量 156', time: '07-29 13:45' },
      { source: 'arXiv 论文', snippet: 'ReAct / Tool Learning 论文 6 篇/周', time: '07-29 13:30' },
    ],
    breakdown: [
      { label: '技能覆盖度', value: 0.76 },
      { label: '出现频次', value: 0.7 },
      { label: '时间新近度', value: 0.92 },
    ],
  },
  {
    id: 'r3',
    name: '数据合规官',
    platform: '智联招聘',
    foundAt: '07-29 12:30',
    confidence: 0.71,
    jdCount: 8,
    description: '负责企业数据治理与合规体系建设，对接《个人信息保护法》《数据安全法》等监管要求。',
    skills: ['数据治理', 'PIPL', 'GDPR', '风险评估'],
    evidence: [
      { source: '智联招聘 JD', snippet: '建立数据合规体系，对接监管检查', time: '07-29 12:25' },
      { source: '政策信号', snippet: '《数据安全法》施行三周年相关招聘上行', time: '07-29 12:00' },
    ],
    breakdown: [
      { label: '技能覆盖度', value: 0.7 },
      { label: '出现频次', value: 0.65 },
      { label: '时间新近度', value: 0.8 },
    ],
  },
  {
    id: 'r4',
    name: '提示词工程师',
    platform: 'Indeed',
    foundAt: '07-29 11:15',
    confidence: 0.83,
    jdCount: 22,
    description: '通过系统化提示词设计与评测，优化大模型在垂直场景下的输出质量，建立可复用的 prompt 模板库。',
    skills: ['Prompt Engineering', 'LLM Eval', 'Python', 'A/B Testing'],
    evidence: [
      { source: 'Indeed JD', snippet: 'Design and iterate prompts for production LLM features', time: '07-29 11:10' },
      { source: 'Stack Overflow', snippet: 'prompt-engineering tag 周环比 +45%', time: '07-29 11:00' },
    ],
    breakdown: [
      { label: '技能覆盖度', value: 0.84 },
      { label: '出现频次', value: 0.8 },
      { label: '时间新近度', value: 0.86 },
    ],
  },
  {
    id: 'r5',
    name: 'MLOps 工程师',
    platform: 'Stack Overflow',
    foundAt: '07-29 10:40',
    confidence: 0.75,
    jdCount: 9,
    description: '构建模型训练-部署-监控闭环，管理特征仓库与模型版本，保障线上模型服务稳定性。',
    skills: ['Kubernetes', 'MLflow', 'Docker', 'Airflow', 'Python'],
    evidence: [
      { source: 'Stack Overflow', snippet: 'mlops 相关提问年增长 2.1x', time: '07-29 10:35' },
      { source: 'GitHub 仓库', snippet: 'mlflow 周下载量 1.2M', time: '07-29 10:20' },
    ],
    breakdown: [
      { label: '技能覆盖度', value: 0.74 },
      { label: '出现频次', value: 0.68 },
      { label: '时间新近度', value: 0.85 },
    ],
  },
  {
    id: 'r6',
    name: 'AIGC 产品经理',
    platform: 'LinkedIn',
    foundAt: '07-29 09:20',
    confidence: 0.69,
    jdCount: 6,
    description: '主导 AIGC 产品从 0 到 1 的规划与落地，平衡模型能力与用户体验，定义可量化的产品指标。',
    skills: ['产品规划', 'AIGC', '用户研究', '数据分析'],
    evidence: [
      { source: 'LinkedIn JD', snippet: 'Drive AI-generated content product roadmap', time: '07-29 09:15' },
    ],
    breakdown: [
      { label: '技能覆盖度', value: 0.66 },
      { label: '出现频次', value: 0.62 },
      { label: '时间新近度', value: 0.84 },
    ],
  },
  {
    id: 'r7',
    name: '向量数据库工程师',
    platform: 'arXiv',
    foundAt: '07-29 08:05',
    confidence: 0.81,
    jdCount: 14,
    description: '负责向量检索引擎的存储、索引与查询优化，支撑亿级向量的高吞吐近邻检索场景。',
    skills: ['HNSW', 'Milvus', 'Pinecone', 'C++', 'ANN'],
    evidence: [
      { source: 'arXiv 论文', snippet: '近 30 天 12 篇 ANN 索引相关论文', time: '07-29 08:00' },
      { source: 'GitHub 仓库', snippet: 'milvus star 周增长 1.2k', time: '07-29 07:50' },
    ],
    breakdown: [
      { label: '技能覆盖度', value: 0.82 },
      { label: '出现频次', value: 0.76 },
      { label: '时间新近度', value: 0.86 },
    ],
  },
  {
    id: 'r8',
    name: 'AI 安全研究员',
    platform: '脉脉',
    foundAt: '07-29 07:30',
    confidence: 0.74,
    jdCount: 5,
    description: '研究大模型对齐、越狱防护与红队对抗，建立模型安全评估基准与防御策略。',
    skills: ['RLHF', 'Red Teaming', '对齐', 'Python'],
    evidence: [
      { source: '脉脉 JD', snippet: '负责大模型安全评估与红队测试', time: '07-29 07:25' },
      { source: 'arXiv 论文', snippet: 'alignment / safety 论文周增 8 篇', time: '07-29 07:00' },
    ],
    breakdown: [
      { label: '技能覆盖度', value: 0.72 },
      { label: '出现频次', value: 0.7 },
      { label: '时间新近度', value: 0.82 },
    ],
  },
]

/** 六状态机当前分布 — 总数 122，与仪表盘「稳定岗位 86」对齐 */
const MOCK_DISTRIBUTION: StateBucket[] = [
  { key: 'candidate', label: '候选', count: 8 },
  { key: 'emerging', label: '新兴', count: 14 },
  { key: 'stable', label: '稳定', count: 86 },
  { key: 'declining', label: '衰退', count: 5 },
  { key: 'archived', label: '归档', count: 3 },
  { key: 'rejected', label: '驳回', count: 6 },
]

/** 最近 5 条审核操作日志 */
const MOCK_LOGS: AuditLog[] = [
  { id: 'l1', admin: 'admin_zhang', action: '批准为 emerging', position: '量化研究员', time: '14:20' },
  { id: 'l2', admin: 'admin_li', action: '驳回为 rejected', position: 'Web3 架构师', time: '13:45' },
  { id: 'l3', admin: 'admin_zhang', action: '批准为 emerging', position: '风控建模工程师', time: '11:30' },
  { id: 'l4', admin: 'admin_wang', action: '确认衰退为 archived', position: '旧版 Flash 工程师', time: '10:15' },
  { id: 'l5', admin: 'admin_li', action: '批准为 emerging', position: '数据治理工程师', time: '09:50' },
]

/** 状态色映射 — candidate 灰/emerging 绿/stable 蓝/declining 橙/archived 归档红/rejected 驳回红 */
const STATE_DOT_CLASS: Record<PositionState, string> = {
  candidate: 'bg-state-candidate',
  emerging: 'bg-state-emerging',
  stable: 'bg-state-stable',
  declining: 'bg-state-declining',
  archived: 'bg-state-archived',
  rejected: 'bg-state-archived',
}

const CONFIDENCE_TONE = (c: number) => (c >= 0.8 ? 'text-state-emerging' : c >= 0.7 ? 'text-state-stable' : 'text-state-declining')

export function AdminReviewPage() {
  const [queue, setQueue] = useState(MOCK_QUEUE)
  const [distribution, setDistribution] = useState(MOCK_DISTRIBUTION)
  const [logs, setLogs] = useState(MOCK_LOGS)
  const [selected, setSelected] = useState<ReviewItem | null>(null)
  // 本次会话审核计数，用于同步今日/本周统计增量
  const [sessionReviewed, setSessionReviewed] = useState(0)

  const totalCount = distribution.reduce((a, b) => a + b.count, 0)

  // 审核动作：移出队列 + 状态机计数迁移 + 日志前置，保证三处视图一致
  function review(item: ReviewItem, action: 'approve' | 'reject') {
    setQueue((q) => q.filter((x) => x.id !== item.id))
    const target = action === 'approve' ? 'emerging' : 'rejected'
    setDistribution((d) =>
      d.map((s) => {
        if (s.key === 'candidate') return { ...s, count: s.count - 1 }
        if (s.key === target) return { ...s, count: s.count + 1 }
        return s
      }),
    )
    setSessionReviewed((n) => n + 1)
    setLogs((l) =>
      [
        {
          id: `log-${Date.now()}`,
          admin: '当前 admin',
          action: action === 'approve' ? '批准为 emerging' : '驳回为 rejected',
          position: item.name,
          time: '刚刚',
        },
        ...l,
      ].slice(0, 5),
    )
    setSelected(null)
  }

  return (
    <>
      <PageHeader title="岗位审核" description="candidate → emerging / rejected · 新兴岗位审批队列" />

      {/* 统计卡 */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <Clock className="size-4 text-ink-faint" />
              <Badge variant="candidate">candidate</Badge>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">{queue.length}</div>
            <div className="text-xs text-ink-muted mt-1">待审核</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <CheckCircle2 className="size-4 text-ink-faint" />
              <span className="text-xs font-mono text-state-emerging">+{sessionReviewed}</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">{12 + sessionReviewed}</div>
            <div className="text-xs text-ink-muted mt-1">今日已审核</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <FileText className="size-4 text-ink-faint" />
              <span className="text-xs font-mono text-state-emerging">+{sessionReviewed}</span>
            </div>
            <div className="text-2xl font-semibold tracking-tight tabular-nums">{68 + sessionReviewed}</div>
            <div className="text-xs text-ink-muted mt-1">本周已审核</div>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 审核队列 */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-sm flex items-center justify-between">
              <span>审核队列</span>
              <span className="text-xs font-normal text-ink-faint">{queue.length} 条待处理</span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>岗位名</TableHead>
                  <TableHead>来源平台</TableHead>
                  <TableHead>发现时间</TableHead>
                  <TableHead>置信度</TableHead>
                  <TableHead className="text-right">JD 数</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {queue.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center text-sm text-ink-faint py-8">
                      队列已清空 · 等待新 candidate 信号
                    </TableCell>
                  </TableRow>
                ) : (
                  queue.map((item) => (
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
                      <TableCell className="text-right">
                        <Button size="sm" variant="ghost" onClick={() => setSelected(item)}>
                          查看详情
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        {/* 状态机看板 + 审核日志 */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">状态机分布</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2.5">
              {distribution.map((s) => {
                const pct = totalCount > 0 ? (s.count / totalCount) * 100 : 0
                return (
                  <div key={s.key}>
                    <div className="flex items-center justify-between text-xs mb-1">
                      <span className="flex items-center gap-2">
                        <span className={`size-2 rounded-full ${STATE_DOT_CLASS[s.key]}`} />
                        {s.label}
                      </span>
                      <span className="font-mono tabular-nums text-ink-muted">
                        {s.count} · {pct.toFixed(1)}%
                      </span>
                    </div>
                    <div className="h-1.5 w-full rounded-full bg-subtle">
                      <div
                        className={`h-full rounded-full ${STATE_DOT_CLASS[s.key]} transition-all`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                )
              })}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm flex items-center justify-between">
                <span>审核操作日志</span>
                <span className="text-xs font-normal text-ink-faint">最近 5 条</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {logs.map((l) => (
                <div key={l.id} className="text-xs flex items-start gap-2">
                  <span className="font-mono text-ink-faint shrink-0 w-12">{l.time}</span>
                  <span className="text-ink-secondary leading-relaxed">
                    <span className="font-medium text-ink">{l.admin}</span> {l.action}
                    <span className="text-ink">「{l.position}」</span>
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* 详情 Dialog — 受控打开，从表格行触发 */}
      <Dialog open={selected !== null} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent className="max-w-2xl max-h-[85vh]">
          <DialogHeader>
            <DialogTitle>{selected?.name}</DialogTitle>
            <DialogDescription>
              来源 {selected?.platform} · 发现于 {selected?.foundAt} · 置信度{' '}
              {selected ? Math.round(selected.confidence * 100) : 0}%
            </DialogDescription>
          </DialogHeader>
          {selected && (
            <div className="space-y-4 max-h-[55vh] overflow-y-auto pr-1">
              <div>
                <p className="text-xs text-ink-muted mb-1">岗位描述</p>
                <p className="text-sm text-ink leading-relaxed">{selected.description}</p>
              </div>
              <div>
                <p className="text-xs text-ink-muted mb-2">关联技能</p>
                <div className="flex flex-wrap gap-1.5">
                  {selected.skills.map((s) => (
                    <Badge key={s} variant="outline">{s}</Badge>
                  ))}
                </div>
              </div>
              <div>
                <p className="text-xs text-ink-muted mb-2">证据列表</p>
                <div className="space-y-2">
                  {selected.evidence.map((e, i) => (
                    <div key={i} className="rounded-md border border-border p-2.5 text-xs">
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-medium text-ink">{e.source}</span>
                        <span className="font-mono text-ink-faint">{e.time}</span>
                      </div>
                      <p className="text-ink-secondary leading-relaxed">{e.snippet}</p>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <p className="text-xs text-ink-muted mb-2">
                  置信度计算明细 · 综合{' '}
                  <span className={CONFIDENCE_TONE(selected.confidence)}>
                    {Math.round(selected.confidence * 100)}%
                  </span>
                </p>
                <div className="space-y-1.5">
                  {selected.breakdown.map((b) => (
                    <div key={b.label} className="flex items-center gap-3 text-xs">
                      <span className="w-20 text-ink-muted shrink-0">{b.label}</span>
                      <div className="flex-1 h-1.5 rounded-full bg-subtle">
                        <div
                          className="h-full rounded-full bg-state-stable"
                          style={{ width: `${b.value * 100}%` }}
                        />
                      </div>
                      <span className="font-mono tabular-nums w-10 text-right">
                        {Math.round(b.value * 100)}%
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
          <DialogFooter className="gap-2">
            <Button variant="destructive" onClick={() => selected && review(selected, 'reject')}>
              驳回为 rejected
            </Button>
            <Button
              className="bg-state-emerging text-canvas hover:opacity-90"
              onClick={() => selected && review(selected, 'approve')}
            >
              批准为 emerging
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
