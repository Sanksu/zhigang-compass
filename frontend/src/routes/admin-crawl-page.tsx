import { useEffect, useRef, useState } from 'react'
import { Activity, Database, Gauge } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
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

/** 后端 /admin/crawl/history 返回项 */
interface CrawlHistoryItem {
  id: string
  platform: string
  platform_name: string
  keyword: string
  status: 'pending' | 'running' | 'success' | 'failed'
  items: number
  error: string
  created_at: string | null
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

/** 后端 /admin/crawl/status 返回项 */
interface CrawlPlatform {
  id: string
  name: string
  level: PlatformLevel
  files: number
  total_count: number
  today_count: number
  last_run: string | null
}

interface CrawlStatusData {
  metrics: {
    today_count: number
    output_total: number
    raw: { jd: number; course: number; paper: number; community: number }
  }
  platforms: CrawlPlatform[]
}

interface MetricCardItem {
  id: string
  label: string
  value: string
  delta: string
  deltaColor: string
  icon: typeof Database
  hint: string
}

/** 平台切换时的默认搜索参数：海外源默认英文城市/英文关键词，国内源默认中文 */
const PLATFORM_DEFAULTS: Record<string, { keyword: string; city: string }> = {
  boss: { keyword: '高级前端', city: '北京' },
  zhilian: { keyword: '高级前端', city: '北京' },
  maimai: { keyword: '高级前端', city: '北京' },
  monster: { keyword: 'Python', city: 'New York' },
  indeed: { keyword: 'Python', city: 'New York' },
  glassdoor: { keyword: 'Python', city: 'New York' },
  linkedin: { keyword: 'Python', city: 'New York' },
}

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
          className="max-h-80 min-h-32 overflow-y-auto rounded-md border border-border bg-ink/[0.03] p-2 font-mono text-[10px] leading-relaxed text-ink-secondary whitespace-pre-wrap"
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
    city: '北京',
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
    apiGet<{ items: CrawlHistoryItem[]; total: number }>('/admin/crawl/history')
      .then((res) =>
        setHistory(
          res.items.map((h) => ({
            id: h.id,
            platformKey: h.platform,
            time: h.created_at ? new Date(h.created_at).toLocaleString('zh-CN') : '—',
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
            lastRun: p.last_run ? new Date(p.last_run).toLocaleString('zh-CN') : '—',
          })),
        )
        setMetrics([
          { id: 'today', label: '今日采集量', value: res.metrics.today_count.toLocaleString(), delta: '今日新增', deltaColor: 'text-state-emerging', icon: Database, hint: '今日 output/*.jsonl 新增行数（CST）' },
          { id: 'output', label: '累计采集量', value: res.metrics.output_total.toLocaleString(), delta: `+${res.platforms.length}源`, deltaColor: 'text-state-emerging', icon: Database, hint: 'output/*.jsonl 真实行数合计' },
          { id: 'raw', label: 'DB 已入库', value: (res.metrics.raw.jd + res.metrics.raw.course).toLocaleString(), delta: `JD ${res.metrics.raw.jd}`, deltaColor: 'text-state-emerging', icon: Activity, hint: 'jd_raw + course_raw 真实计数' },
          { id: 'files', label: '采集文件数', value: res.platforms.length.toLocaleString(), delta: '13 源', deltaColor: 'text-ink-muted', icon: Gauge, hint: '有采集记录的平台数' },
        ])
      })
      .catch(() => {
        setMetrics([
          { id: 'today', label: '今日采集量', value: '—', delta: '—', deltaColor: 'text-ink-muted', icon: Database, hint: '状态加载失败' },
          { id: 'output', label: '累计采集量', value: '—', delta: '—', deltaColor: 'text-ink-muted', icon: Database, hint: '请确认后端服务已启动' },
          { id: 'raw', label: 'DB 已入库', value: '—', delta: '—', deltaColor: 'text-ink-muted', icon: Activity, hint: '—' },
          { id: 'files', label: '采集文件数', value: '—', delta: '—', deltaColor: 'text-ink-muted', icon: Gauge, hint: '—' },
        ])
      })
      .finally(() => setLoading(false))
  }, [])

  // 任务未完成时禁用所有触发入口，避免并发任务导致调度混乱
  const isBusy = currentTask !== null && currentTask.status !== 'done' && currentTask.status !== 'failed'

  // 触发爬取 → 真实 POST /admin/crawl/trigger（ARQ 入队，202 返回 task_id）
  async function triggerCrawl(platform: string) {
    if (isBusy) return
    const def = PLATFORM_DEFAULTS[platform]
    const keyword = form.keyword?.trim() || def?.keyword || '高级前端'
    if (!platform || !keyword) {
      setNotice('请选择平台并填写关键词')
      return
    }
    setCurrentTask({
      platform: platforms.find((p) => p.id === platform)?.name ?? platform,
      keyword,
      city: form.city || def?.city || '北京',
      maxPages: form.maxPages || 30,
      status: 'queued',
      progress: 0,
      collected: 0,
      total: 0,
      logs: [],
    })
    setNotice(null)
    try {
      const res = await apiPost<{ task_id: string; platform: string; status: string }>('/admin/crawl/trigger', {
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
      <PageHeader title="爬取管理" description="手动触发 13 源采集 · 进度监控 · 历史回溯" />

      {/* 触发结果通知 */}
      {notice && (
        <div className="mb-4 rounded-md border border-state-candidate/20 bg-state-candidate/5 px-4 py-3 text-sm text-state-candidate">
          {notice}
        </div>
      )}

      {/* 顶部指标卡（真实 raw 表 + output 统计） */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {metrics.map((m) => {
          const Icon = m.icon
          return (
            <Card key={m.id}>
              <CardContent className="py-4">
                <div className="flex items-center justify-between mb-2">
                  <Icon className="size-4 text-ink-faint" />
                  <span className={`text-xs font-mono ${m.deltaColor}`}>{m.delta}</span>
                </div>
                <div className="text-2xl font-semibold tracking-tight tabular-nums">{m.value}</div>
                <div className="text-xs text-ink-muted mt-1">{m.label}</div>
                <div className="text-[10px] text-ink-faint mt-0.5 truncate">{m.hint}</div>
              </CardContent>
            </Card>
          )
        })}
      </div>

      {/* 平台状态表 */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="text-sm flex items-center justify-between">
            <span>平台状态</span>
            <span className="text-xs font-normal text-ink-faint">13 源在线</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="py-8 text-center text-sm text-ink-muted">加载真实采集状态…</p>
          ) : (
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
                  const meta = STATUS_META[p.status]
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
          )}
        </CardContent>
      </Card>

      {/* 手动触发表单 + 当前任务进度 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">手动触发爬取</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>平台</Label>
                <Select
                  value={form.platform}
                  onValueChange={(v) =>
                    setForm((f) => {
                      const def = PLATFORM_DEFAULTS[v]
                      const prevDef = PLATFORM_DEFAULTS[f.platform]
                      // 关键词/城市未手动输入（为空或仍为上一平台默认值）时，跟随新平台默认
                      const keyword =
                        def && (!f.keyword.trim() || f.keyword === prevDef?.keyword) ? def.keyword : f.keyword
                      const city =
                        def && (!f.city.trim() || f.city === prevDef?.city) ? def.city : f.city
                      return { ...f, platform: v, keyword, city }
                    })
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
                  placeholder={PLATFORM_DEFAULTS[form.platform]?.keyword ?? '高级前端'}
                />
              </div>
              <div className="space-y-1.5">
                <Label>城市</Label>
                <Input
                  value={form.city}
                  onChange={(e) => setForm((f) => ({ ...f, city: e.target.value }))}
                  placeholder={PLATFORM_DEFAULTS[form.platform]?.city ?? '北京'}
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

        <Card>
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
                <div className="grid grid-cols-2 gap-2 text-xs">
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
                    className="max-h-48 overflow-y-auto rounded-md border border-border bg-ink/[0.03] p-2 font-mono text-[10px] leading-relaxed text-ink-secondary whitespace-pre-wrap"
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
      </div>

      {/* 历史记录 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center justify-between">
            <span>历史记录</span>
            <span className="text-xs font-normal text-ink-faint">{history.length} 条</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
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
        </CardContent>
      </Card>

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
            <div className="grid grid-cols-2 gap-3 text-sm">
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
