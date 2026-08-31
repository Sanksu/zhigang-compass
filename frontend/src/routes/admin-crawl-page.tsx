import { useEffect, useRef, useState } from 'react'
import { Activity, Database, Gauge } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Reveal } from '@/components/ui/reveal'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { CrawlScheduleConfig } from '@/components/admin/crawl-schedule-config'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
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
import { apiGet, apiPost, ApiError, getAccessToken } from '@/lib/api'
import { formatDateTime } from '@/lib/utils'
import { MetricCard, type MetricCardData } from '@/components/shared/metric-card'
import type { components } from '@/types/api'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

/** 爬虫运行状态 — 对应 §4 平台采集生命周期 */
type CrawlStatus = 'running' | 'idle' | 'failed' | 'archived'

/** 平台分级 — 对齐设计文档 §4 的 A/B/C + 信号 + 课程 */
type PlatformLevel = 'A' | 'B' | 'C' | '信号' | '论文' | '课程'

interface PlatformRow {
  id: string
  name: string
  level: PlatformLevel
  status: CrawlStatus
  todayCount: number
  totalCount: number
  lastRun: string
}

interface HistoryRow {
  id: string
  /** 原始 spider 名（日志弹窗按平台匹配最近任务） */
  platformKey: string
  time: string
  platform: string
  keyword: string
  count: number
  status: 'pending' | 'running' | 'success' | 'failed'
  error: string
}

interface CurrentTask {
  platform: string
  keyword: string
  city: string
  maxPages: number
  status: 'queued' | 'running' | 'done' | 'failed'
  progress: number
  collected: number
  total: number
  /** 实时爬虫日志（SSE 逐行推送） */
  logs: string[]
  taskId?: string
}

/** 后端 /admin/crawl/status 响应 data（契约 CrawlStatusData） */
type CrawlStatusData = components['schemas']['CrawlStatusData']

/** 指标卡数据 —— 共享 MetricCard 形态 + id（渲染 key 用） */
type MetricCardItem = MetricCardData & { id: string }

// 08-16 用户决策：无平台默认关键词/城市——留空 = 平台热度/最新且不限城市

const STATUS_META: Record<CrawlStatus, { variant: 'stable' | 'candidate' | 'archived'; label: string }> = {
  running: { variant: 'stable', label: '运行中' },
  idle: { variant: 'candidate', label: '空闲' },
  failed: { variant: 'archived', label: '失败' },
  archived: { variant: 'archived', label: '归档' },
}

const HISTORY_STATUS_META: Record<HistoryRow['status'], { variant: 'stable' | 'candidate' | 'declining' | 'archived'; label: string }> = {
  success: { variant: 'stable', label: '成功' },
  running: { variant: 'declining', label: '运行中' },
  pending: { variant: 'candidate', label: '队列中' },
  failed: { variant: 'archived', label: '失败' },
}

/** 等级配色 — 与 Badge outline 叠加，A 绿/B 蓝/C 橙突出分级语义 */
const LEVEL_CLASS: Record<PlatformLevel, string> = {
  A: 'text-state-emerging border-state-emerging/30',
  B: 'text-state-stable border-state-stable/30',
  C: 'text-state-declining border-state-declining/30',
  '信号': 'text-ink-secondary border-border-strong',
  '论文': 'text-ink-secondary border-border-strong',
  '课程': 'text-ink-muted border-border',
}

/**
 * 平台实时状态从真实爬取历史推导（08-14 审查：此前 status:'idle' 恒硬编码，
 * "数据源运行状态"表永远显示"空闲"；后端 /admin/crawl/status 无 per-platform 状态，
 * 用最近任务状态近似——running/pending → 运行中，failed → 失败，其余 → 空闲）
 */
function platformStatus(p: PlatformRow, history: HistoryRow[]): CrawlStatus {
  const last = history.find((h) => h.platformKey === p.id)
  if (last?.status === 'running' || last?.status === 'pending') return 'running'
  if (last?.status === 'failed') return 'failed'
  return 'idle'
}

/** SSE 日志读取结果：done=任务成功 / failed=任务失败 / closed=流结束（可能仍在后台执行） */
interface SseLogResult {
  status: 'done' | 'failed' | 'closed'
  message?: string
}

/**
 * 读取爬虫实时日志 SSE（GET /admin/crawl/task/{taskId}/stream），onLog 逐行回调。
 * 10 分钟无终态事件则中止（与后端 600s 兜底一致；短任务如 boss 可达数分钟，
 * 60s 会误断导致"连接中断或超时"）；
 * admin 端点需认证，fetch 不会自动附加 token，手动加 Bearer。
 */
async function readSseCrawlLog(taskId: string, onLog: (line: string) => void, signal?: AbortSignal): Promise<SseLogResult> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), 600_000)
  // 外部 signal（组件卸载/弹窗关闭）触发时联动中止内部 ctrl，避免 SSE 连接泄漏
  const onAbort = () => ctrl.abort()
  if (signal) {
    if (signal.aborted) ctrl.abort()
    else signal.addEventListener('abort', onAbort, { once: true })
  }
  try {
    const headers: Record<string, string> = {}
    const token = getAccessToken()
    if (token) headers.Authorization = `Bearer ${token}`
    const resp = await fetch(`/api/v1/admin/crawl/task/${taskId}/stream`, {
      signal: ctrl.signal,
      headers,
    })
    if (!resp.ok || !resp.body) return { status: 'closed', message: 'SSE 连接失败' }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      // 按 SSE 帧分隔解析 event/data
      const frames = buffer.split('\n\n')
      buffer = frames.pop() ?? ''
      for (const frame of frames) {
        const event = frame.match(/^event:\s*(.+)$/m)?.[1]
        const data = frame.match(/^data:\s*(.+)$/m)?.[1]
        if (!data) continue
        const payload = (() => {
          try {
            return JSON.parse(data)
          } catch {
            return {}
          }
        })()
        if (event === 'log') {
          const line = String(payload.line ?? '')
          if (line) onLog(line)
        } else if (event === 'done') {
          return { status: 'done' }
        } else if (event === 'error') {
          return { status: 'failed', message: String(payload.message ?? payload.error ?? '任务执行失败') }
        }
      }
    }
    return { status: 'closed' }
  } catch {
    return { status: 'closed', message: '连接中断或超时' }
  } finally {
    clearTimeout(timer)
    if (signal) signal.removeEventListener('abort', onAbort)
  }
}

/** 平台日志弹窗：SSE 实时/回溯展示该平台最近一次爬取任务的日志 */
function CrawlLogDialog({ taskId, platformName, onClose }: {
  taskId: string | null
  platformName: string
  onClose: () => void
}) {
  const [lines, setLines] = useState<string[]>([])
  // 初始状态由 taskId 派生（key 保证重新挂载，无需在 effect 里重置）
  const [status, setStatus] = useState<'loading' | 'done' | 'failed' | 'empty'>(taskId ? 'loading' : 'empty')
  const [message, setMessage] = useState('')
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!taskId) return
    let cancelled = false
    const ctrl = new AbortController()
    readSseCrawlLog(taskId, (ln) => {
      if (!cancelled) setLines((prev) => [...prev, ln].slice(-300))
    }, ctrl.signal).then((r) => {
      if (cancelled) return
      if (r.status === 'failed') {
        setStatus('failed')
        setMessage(r.message ?? '任务执行失败')
      } else {
        setStatus('done')
        setMessage(r.message ?? '')
      }
    })
    return () => {
      // 弹窗关闭/组件卸载时中止 SSE 连接，避免挂起到 600s 超时
      cancelled = true
      ctrl.abort()
    }
  }, [taskId])

  useEffect(() => {
    if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight
  }, [lines.length])

  const statusText =
    status === 'loading' ? '实时推送中' : status === 'done' ? '已结束' : status === 'failed' ? '失败' : '无记录'
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>爬虫日志 · {platformName}</DialogTitle>
          <DialogDescription>
            {taskId ? `task_id ${taskId.slice(0, 8)}… · ${statusText}` : '该平台暂无爬取记录'}
          </DialogDescription>
        </DialogHeader>
        <div
          ref={boxRef}
          className="max-h-80 min-h-32 overflow-y-auto rounded-md border border-border bg-ink/[0.03] p-2 font-mono text-[11px] leading-relaxed text-ink-secondary whitespace-pre-wrap"
        >
          {status === 'empty' ? (
            <span className="text-ink-faint">该平台暂无爬取记录</span>
          ) : lines.length === 0 ? (
            <span className="text-ink-faint">
              {status === 'loading' ? '等待日志…' : '日志为空（Redis 日志 TTL 1h，可能已过期）'}
            </span>
          ) : (
            lines.map((ln, i) => <div key={i}>{ln}</div>)
          )}
        </div>
        {status === 'failed' && message && (
          <p className="text-xs text-state-archived whitespace-pre-wrap">{message}</p>
        )}
      </DialogContent>
    </Dialog>
  )
}

export function AdminCrawlPage() {
  const [form, setForm] = useState({
    platform: 'boss',
    keyword: '',
    city: '',
    maxPages: 30,
  })
  const [currentTask, setCurrentTask] = useState<CurrentTask | null>(null)
  const [platforms, setPlatforms] = useState<PlatformRow[]>([])
  const [metrics, setMetrics] = useState<MetricCardItem[]>([])
  const [loading, setLoading] = useState(true)
  const [notice, setNotice] = useState<string | null>(null)
  const [history, setHistory] = useState<HistoryRow[]>([])
  /** 日志弹窗：{platformName, taskId}，taskId 为该平台最近任务的 id（无记录为 null） */
  const [logDialog, setLogDialog] = useState<{ platformName: string; taskId: string | null } | null>(null)
  const [detailRow, setDetailRow] = useState<HistoryRow | null>(null)

  // 实时日志滚动区：新日志到达自动滚到底部
  const logRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [currentTask?.logs.length])

  // 触发后 SSE 连接的中止句柄：组件卸载时中止，避免连接挂起到 600s 超时
  const sseAbortRef = useRef<AbortController | null>(null)
  useEffect(() => () => sseAbortRef.current?.abort(), [])

  // 加载爬取历史（真实 /admin/crawl/history，task_status 倒序）
  useEffect(() => {
    apiGet<components['schemas']['CrawlHistoryData']>('/admin/crawl/history')
      .then((res) =>
        setHistory(
          res.items.map((h) => ({
            id: h.id,
            platformKey: h.platform,
            time: formatDateTime(h.created_at),
            platform: h.platform_name || h.platform || '—',
            keyword: h.keyword || '—',
            count: h.items,
            status: h.status,
            error: h.error,
          })),
        ),
      )
      .catch(() => {
        /* 历史加载失败不阻塞页面 */
      })
  }, [])

  // 加载真实爬虫采集状态（raw 表 + output JSONL 统计）
  useEffect(() => {
    apiGet<CrawlStatusData>('/admin/crawl/status')
      .then((res) => {
        setPlatforms(
          res.platforms.map((p) => ({
            id: p.id,
            name: p.name,
            level: p.level,
            status: 'idle',
            todayCount: p.today_count ?? 0,
            totalCount: p.total_count,
            lastRun: formatDateTime(p.last_run),
          })),
        )
        setMetrics([
          { id: 'today', label: '今日采集量', value: res.metrics.today_count.toLocaleString(), delta: '今日新增', deltaTone: 'emerging', icon: Database, hint: '今日 DB 入库新增（CST）' },
          // 累计采集量统一 DB 口径（08-15 用户决策）：与仪表盘一致的四表入库总量
          { id: 'total', label: '累计采集量', value: (res.metrics.raw_total ?? 0).toLocaleString(), delta: `+${res.platforms.length}源`, deltaTone: 'emerging', icon: Database, hint: 'DB 入库总量（jd/course/paper/community）· 与仪表盘口径一致' },
          { id: 'raw', label: 'JD/课程入库', value: (res.metrics.raw.jd + res.metrics.raw.course).toLocaleString(), delta: `JD ${res.metrics.raw.jd}`, deltaTone: 'emerging', icon: Activity, hint: 'jd_raw + course_raw 细分计数' },
          { id: 'files', label: '有记录平台', value: res.platforms.length.toLocaleString(), delta: `${res.platforms.length} 源`, deltaTone: 'muted', icon: Gauge, hint: '有采集记录的平台数' },
        ])
      })
      .catch(() => {
        setMetrics([
          { id: 'today', label: '今日采集量', value: '—', delta: '—', deltaTone: 'muted', icon: Database, hint: '状态加载失败' },
          { id: 'total', label: '累计采集量', value: '—', delta: '—', deltaTone: 'muted', icon: Database, hint: '请确认后端服务已启动' },
          { id: 'raw', label: 'JD/课程入库', value: '—', delta: '—', deltaTone: 'muted', icon: Activity, hint: '—' },
          { id: 'files', label: '有记录平台', value: '—', delta: '—', deltaTone: 'muted', icon: Gauge, hint: '—' },
        ])
      })
      .finally(() => setLoading(false))
  }, [])

  // 任务未完成时禁用所有触发入口，避免并发任务导致调度混乱
  const isBusy = currentTask !== null && currentTask.status !== 'done' && currentTask.status !== 'failed'

  // 触发爬取 → 真实 POST /admin/crawl/trigger（ARQ 入队，202 返回 task_id）
  async function triggerCrawl(platform: string) {
    if (isBusy) return
    const keyword = form.keyword?.trim() ?? ''
    if (!platform) {
      setNotice('请选择平台')
      return
    }
    setCurrentTask({
      platform: platforms.find((p) => p.id === platform)?.name ?? platform,
      keyword,
      city: form.city?.trim() || '',
      maxPages: form.maxPages || 30,
      status: 'queued',
      progress: 0,
      collected: 0,
      total: 0,
      logs: [],
    })
    setNotice(null)
    try {
      const res = await apiPost<components['schemas']['CrawlTriggerResult']>('/admin/crawl/trigger', {
        platform,
        keyword,
        city: form.city?.trim() || '',
      })
      setCurrentTask((t) => (t ? { ...t, taskId: res.task_id, status: 'running', progress: 10 } : t))
      setNotice(`爬取任务已入队（task_id: ${res.task_id.slice(0, 8)}…），等待 worker 执行`)
      await streamCrawlLogs(res.task_id)
    } catch (e) {
      setCurrentTask(null)
      setNotice(e instanceof ApiError ? `触发失败：${e.message}` : '触发失败，请检查后端与 Redis 队列')
    }
  }

  // 订阅爬虫实时日志 SSE（触发后自动展示在当前任务卡片）
  async function streamCrawlLogs(taskId: string) {
    const ctrl = new AbortController()
    sseAbortRef.current = ctrl
    try {
      const result = await readSseCrawlLog(taskId, (line) => {
        setCurrentTask((t) => (t ? { ...t, logs: [...t.logs, line].slice(-200) } : t))
      }, ctrl.signal)
      if (result.status === 'done') {
        setNotice('爬取任务执行完成，数据已写入 output/*.jsonl')
        setCurrentTask((t) => (t ? { ...t, status: 'done', progress: 100 } : t))
      } else if (result.status === 'failed') {
        setNotice(`爬取任务失败：${result.message}`)
        setCurrentTask((t) => (t ? { ...t, status: 'failed' } : t))
      } else {
        setNotice(result.message ?? '日志推送结束（任务可能在后台继续执行），可查看 output 文件确认结果')
        setCurrentTask((t) =>
          t && t.status !== 'done' && t.status !== 'failed' ? { ...t, status: 'done' } : t,
        )
      }
    } finally {
      sseAbortRef.current = null
    }
  }

  return (
    <>
      <PageHeader title="爬取管理" description="手动触发多源采集 · 进度监控 · 历史回溯" />

      {/* 触发结果通知 */}
      {notice && (
        <div className="mb-4 rounded-md border border-state-candidate/20 bg-state-candidate/5 px-4 py-3 text-sm text-state-candidate">
          {notice}
        </div>
      )}

      {/* 顶部指标卡（真实 raw 表 + output 统计） */}
      <Tabs defaultValue="realtime">
        <TabsList className="mb-4">
          <TabsTrigger value="realtime" className="text-xs">实时与历史</TabsTrigger>
          <TabsTrigger value="schedule" className="text-xs">调度与限频</TabsTrigger>
        </TabsList>

        <TabsContent value="realtime">
          {/* 顶部指标卡（真实 raw 表 + output 统计）——共享 MetricCard 统一形态 */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            {metrics.map((m, i) => (
              <Reveal key={m.id} delay={i * 90} className="h-full">
                <MetricCard data={m} className="h-full" />
              </Reveal>
            ))}
          </div>

          {/* 平台状态表 */}
          <Reveal delay={380}>
          <Card className="mb-6">
            <CardHeader>
              <CardTitle className="text-sm flex items-center justify-between">
                <span>平台状态</span>
                <span className="text-xs font-normal text-ink-faint">{platforms.length} 源在线</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {loading ? (
                <p className="py-8 text-center text-sm text-ink-muted">加载真实采集状态…</p>
              ) : (
                <>
                  {/* 桌面端：表格视图 */}
                  <div className="hidden lg:block">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>平台名</TableHead>
                          <TableHead>等级</TableHead>
                          <TableHead>状态</TableHead>
                          <TableHead className="text-right">今日采集</TableHead>
                          <TableHead className="text-right">累计采集</TableHead>
                          <TableHead>最后运行</TableHead>
                          <TableHead className="text-right">操作</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {platforms.map((p) => {
                          const meta = STATUS_META[platformStatus(p, history)]
                          return (
                            <TableRow key={p.id} className={p.status === 'archived' ? 'opacity-50' : ''}>
                              <TableCell className="font-medium">{p.name}</TableCell>
                              <TableCell>
                                <Badge variant="outline" className={LEVEL_CLASS[p.level]}>{p.level}</Badge>
                              </TableCell>
                              <TableCell>
                                <Badge variant={meta.variant}>{meta.label}</Badge>
                              </TableCell>
                              <TableCell className="text-right tabular-nums font-mono">{p.todayCount.toLocaleString()}</TableCell>
                              <TableCell className="text-right tabular-nums font-mono text-ink-muted">{p.totalCount.toLocaleString()}</TableCell>
                              <TableCell className="text-xs text-ink-muted font-mono">{p.lastRun}</TableCell>
                              <TableCell className="text-right">
                                <div className="flex items-center justify-end gap-2">
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    disabled={p.status === 'archived' || isBusy}
                                    onClick={() => triggerCrawl(p.id)}
                                  >
                                    触发
                                  </Button>
                                  <Button size="sm" variant="ghost" onClick={() => setLogDialog({
                                    platformName: p.name,
                                    taskId: history.find((h) => h.platformKey === p.id)?.id ?? null,
                                  })}>日志</Button>
                                </div>
                              </TableCell>
                            </TableRow>
                          )
                        })}
                        {platforms.length === 0 && (
                          <TableRow>
                            <TableCell colSpan={7} className="text-center text-sm text-ink-faint py-8">
                              暂无采集记录，请先运行爬虫
                            </TableCell>
                          </TableRow>
                        )}
                      </TableBody>
                    </Table>
                  </div>

                  {/* 移动端：卡片视图 */}
                  <div className="space-y-3 lg:hidden">
                    {platforms.map((p) => {
                      const meta = STATUS_META[platformStatus(p, history)]
                      return (
                        <div
                          key={p.id}
                          className={cn(
                            'rounded-lg border border-border bg-canvas p-4 space-y-3',
                            p.status === 'archived' && 'opacity-50',
                          )}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2">
                                <span className="font-medium text-ink">{p.name}</span>
                                <Badge variant="outline" className={LEVEL_CLASS[p.level]}>{p.level}</Badge>
                              </div>
                              <div className="mt-1">
                                <Badge variant={meta.variant}>{meta.label}</Badge>
                              </div>
                            </div>
                            <div className="text-right">
                              <div className="text-lg font-semibold tabular-nums font-mono text-ink">
                                {p.todayCount.toLocaleString()}
                              </div>
                              <div className="text-[11px] text-ink-faint">今日采集</div>
                            </div>
                          </div>
                          <div className="flex items-center justify-between text-xs text-ink-faint">
                            <span>累计 {p.totalCount.toLocaleString()}</span>
                            <span className="font-mono">{p.lastRun}</span>
                          </div>
                          <div className="flex flex-wrap gap-1.5 pt-1">
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={p.status === 'archived' || isBusy}
                              onClick={() => triggerCrawl(p.id)}
                            >
                              触发采集
                            </Button>
                            <Button size="sm" variant="outline" onClick={() => setLogDialog({
                              platformName: p.name,
                              taskId: history.find((h) => h.platformKey === p.id)?.id ?? null,
                            })}>查看日志</Button>
                          </div>
                        </div>
                      )
                    })}
                    {platforms.length === 0 && (
                      <div className="py-8 text-center text-sm text-ink-faint">
                        暂无采集记录，请先运行爬虫
                      </div>
                    )}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
          </Reveal>
    
          {/* 手动触发表单 + 当前任务进度 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
            <Reveal delay={500} className="h-full">
            <Card className="h-full">
              <CardHeader>
                <CardTitle className="text-sm">手动触发爬取</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label>平台</Label>
                    <Select
                      value={form.platform}
                      onValueChange={(v) =>
                        setForm((f) => ({
                          // 关键词/城市均无平台默认（留空 = 热度/最新且不限城市，08-16 用户决策）
                          ...f,
                          platform: v,
                        }))
                      }
                    >
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {platforms.filter((p) => p.status !== 'archived').map((p) => (
                          <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label>关键词</Label>
                    <Input
                      value={form.keyword}
                      onChange={(e) => setForm((f) => ({ ...f, keyword: e.target.value }))}
                      placeholder="留空则采集平台热度/最新内容"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label>城市</Label>
                    <Input
                      value={form.city}
                      onChange={(e) => setForm((f) => ({ ...f, city: e.target.value }))}
                      placeholder="留空则不限城市"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label>页数上限</Label>
                    <Input
                      type="number"
                      min={1}
                      max={100}
                      value={form.maxPages}
                      onChange={(e) => setForm((f) => ({ ...f, maxPages: Number(e.target.value) || 0 }))}
                    />
                  </div>
                </div>
                <Button className="w-full" disabled={isBusy} onClick={() => triggerCrawl(form.platform)}>
                  {isBusy ? '任务进行中…' : '触发爬取'}
                </Button>
              </CardContent>
            </Card>
            </Reveal>
    
            <Reveal delay={620} className="h-full">
            <Card className="h-full">
              <CardHeader>
                <CardTitle className="text-sm flex items-center justify-between">
                  <span>当前任务</span>
                  {currentTask && (
                    <Badge
                      variant={currentTask.status === 'running' ? 'stable' : currentTask.status === 'failed' ? 'archived' : 'candidate'}
                    >
                      {currentTask.status === 'queued'
                        ? '队列中'
                        : currentTask.status === 'running'
                          ? '运行中'
                          : currentTask.status === 'failed'
                            ? '失败'
                            : '完成'}
                    </Badge>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {!currentTask ? (
                  <div className="flex items-center justify-center py-8 text-sm text-ink-faint">
                    暂无运行中的任务 · 点击「触发爬取」开始
                  </div>
                ) : (
                  <div className="space-y-3">
                    <div className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
                      <div><span className="text-ink-muted">平台：</span><span className="font-medium">{currentTask.platform}</span></div>
                      <div><span className="text-ink-muted">关键词：</span><span className="font-medium">{currentTask.keyword}</span></div>
                      <div><span className="text-ink-muted">城市：</span><span className="font-medium">{currentTask.city}</span></div>
                      <div><span className="text-ink-muted">页数上限：</span><span className="font-mono">{currentTask.maxPages}</span></div>
                    </div>
                    <div>
                      <div className="flex items-center justify-between text-xs mb-1">
                        <span className="text-ink-muted">进度</span>
                        <span className="font-mono tabular-nums">
                          {currentTask.collected}/{currentTask.total} · {currentTask.progress}%
                        </span>
                      </div>
                      <div className="h-2 w-full rounded-full bg-subtle overflow-hidden">
                        <div
                          className={`h-full transition-all duration-500 ${
                            currentTask.status === 'failed' ? 'bg-state-archived' : 'bg-state-stable'
                          }`}
                          style={{ width: `${currentTask.progress}%` }}
                        />
                      </div>
                    </div>
                    {/* 实时日志（SSE 逐行推送 scrapy 输出） */}
                    <div>
                      <div className="flex items-center justify-between text-xs mb-1">
                        <span className="text-ink-muted">实时日志</span>
                        <span className="font-mono tabular-nums text-ink-faint">{currentTask.logs.length} 行</span>
                      </div>
                      <div
                        ref={logRef}
                        className="max-h-48 overflow-y-auto rounded-md border border-border bg-ink/[0.03] p-2 font-mono text-[11px] leading-relaxed text-ink-secondary whitespace-pre-wrap"
                      >
                        {currentTask.logs.length === 0 ? (
                          <span className="text-ink-faint">等待 worker 执行，日志将在此实时显示…</span>
                        ) : (
                          currentTask.logs.map((ln, i) => <div key={i}>{ln}</div>)
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
            </Reveal>
          </div>
    
          {/* 历史记录 */}
          <Reveal delay={740}>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm flex items-center justify-between">
                <span>历史记录</span>
                <span className="text-xs font-normal text-ink-faint">{history.length} 条</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {/* 桌面端：表格视图 */}
              <div className="hidden lg:block">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>时间</TableHead>
                      <TableHead>平台</TableHead>
                      <TableHead>关键词</TableHead>
                      <TableHead className="text-right">采集数</TableHead>
                      <TableHead>状态</TableHead>
                      <TableHead>错误/说明</TableHead>
                      <TableHead className="text-right">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {history.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={7} className="text-center text-sm text-ink-faint py-8">
                          暂无爬取记录，点击上方「触发爬取」开始
                        </TableCell>
                      </TableRow>
                    ) : (
                      history.map((h) => {
                        const meta = HISTORY_STATUS_META[h.status]
                        return (
                          <TableRow key={h.id}>
                            <TableCell className="text-xs font-mono text-ink-muted">{h.time}</TableCell>
                            <TableCell className="font-medium">{h.platform}</TableCell>
                            <TableCell className="text-ink-secondary">{h.keyword}</TableCell>
                            <TableCell className="text-right tabular-nums font-mono">{h.count}</TableCell>
                            <TableCell><Badge variant={meta.variant}>{meta.label}</Badge></TableCell>
                            <TableCell className="text-xs text-ink-muted truncate max-w-[220px]">{h.error || '—'}</TableCell>
                            <TableCell className="text-right">
                              <Button size="sm" variant="ghost" onClick={() => setDetailRow(h)}>详情</Button>
                            </TableCell>
                          </TableRow>
                        )
                      })
                    )}
                  </TableBody>
                </Table>
              </div>

              {/* 移动端：卡片视图 */}
              <div className="space-y-3 lg:hidden">
                {history.length === 0 ? (
                  <div className="py-8 text-center text-sm text-ink-faint">
                    暂无爬取记录，点击上方「触发爬取」开始
                  </div>
                ) : (
                  history.map((h) => {
                    const meta = HISTORY_STATUS_META[h.status]
                    return (
                      <div
                        key={h.id}
                        className="rounded-lg border border-border bg-canvas p-4 space-y-2"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <span className="font-medium text-ink">{h.platform}</span>
                              <Badge variant={meta.variant}>{meta.label}</Badge>
                            </div>
                            <div className="text-[11px] text-ink-faint font-mono mt-0.5">{h.time}</div>
                          </div>
                          <div className="text-right">
                            <div className="text-lg font-semibold tabular-nums font-mono text-ink">
                              {h.count}
                            </div>
                            <div className="text-[11px] text-ink-faint">采集数</div>
                          </div>
                        </div>
                        <div className="flex items-center gap-3 text-xs text-ink-muted">
                          <span>关键词：<span className="text-ink-secondary">{h.keyword}</span></span>
                        </div>
                        {h.error && (
                          <p className="text-xs text-ink-muted border-t border-border pt-1.5 truncate">{h.error}</p>
                        )}
                        <Button size="sm" variant="outline" className="w-full" onClick={() => setDetailRow(h)}>
                          查看详情
                        </Button>
                      </div>
                    )
                  })
                )}
              </div>
            </CardContent>
          </Card>
          </Reveal>
        </TabsContent>

        {/* 调度与限频：采集上限 + 限频 + 每爬虫配置（08-27 从 settings 迁入，爬虫域一处管全） */}
        <TabsContent value="schedule">
          <Reveal delay={380}>
            <CrawlScheduleConfig />
          </Reveal>
        </TabsContent>
      </Tabs>

      {/* 平台日志弹窗（SSE 实时/回溯） */}
      {logDialog && (
        <CrawlLogDialog
          key={logDialog.taskId ?? 'empty'}
          taskId={logDialog.taskId}
          platformName={logDialog.platformName}
          onClose={() => setLogDialog(null)}
        />
      )}

      {/* 任务详情弹窗 */}
      {detailRow && (
        <Dialog open onOpenChange={(o) => { if (!o) setDetailRow(null) }}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>任务详情</DialogTitle>
              <DialogDescription>task_id: {detailRow.id}</DialogDescription>
            </DialogHeader>
            <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
              <div>
                <div className="text-xs text-ink-muted mb-0.5">时间</div>
                <div className="font-mono text-xs text-ink-secondary">{detailRow.time}</div>
              </div>
              <div>
                <div className="text-xs text-ink-muted mb-0.5">平台</div>
                <div className="font-medium">{detailRow.platform}</div>
              </div>
              <div>
                <div className="text-xs text-ink-muted mb-0.5">关键词</div>
                <div className="text-ink-secondary">{detailRow.keyword}</div>
              </div>
              <div>
                <div className="text-xs text-ink-muted mb-0.5">采集数</div>
                <div className="font-mono tabular-nums">{detailRow.count}</div>
              </div>
              <div className="col-span-2">
                <div className="text-xs text-ink-muted mb-1">状态</div>
                <Badge variant={HISTORY_STATUS_META[detailRow.status].variant}>
                  {HISTORY_STATUS_META[detailRow.status].label}
                </Badge>
              </div>
              {detailRow.error && (
                <div className="col-span-2">
                  <div className="text-xs text-ink-muted mb-1">错误/说明</div>
                  <div className="rounded-md border border-border bg-ink/[0.03] p-2 text-xs whitespace-pre-wrap text-state-archived">
                    {detailRow.error}
                  </div>
                </div>
              )}
            </div>
          </DialogContent>
        </Dialog>
      )}
    </>
  )
}
