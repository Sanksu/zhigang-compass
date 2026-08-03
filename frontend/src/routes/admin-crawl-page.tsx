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

  // 实时日志滚动区：新日志到达自动滚到底部
  const logRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [currentTask?.logs.length])

  // 加载爬取历史（真实 /admin/crawl/history，task_status 倒序）
  useEffect(() => {
    apiGet<{ items: CrawlHistoryItem[]; total: number }>('/admin/crawl/history')
      .then((res) =>
        setHistory(
          res.items.map((h) => ({
            id: h.id,
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
            todayCount: 0,
            totalCount: p.total_count,
            lastRun: p.last_run ? new Date(p.last_run).toLocaleString('zh-CN') : '—',
          })),
        )
        setMetrics([
          { id: 'today', label: '今日采集量', value: '0', delta: '调度后统计', deltaColor: 'text-ink-muted', icon: Database, hint: 'ETL 调度（DA-M2-12）接入后统计增量' },
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
    const keyword = form.keyword?.trim() || '高级前端'
    if (!platform || !keyword) {
      setNotice('请选择平台并填写关键词')
      return
    }
    setCurrentTask({
      platform: platforms.find((p) => p.id === platform)?.name ?? platform,
      keyword,
      city: form.city || '北京',
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
      })
      setCurrentTask((t) => (t ? { ...t, taskId: res.task_id, status: 'running', progress: 10 } : t))
      setNotice(`爬取任务已入队（task_id: ${res.task_id.slice(0, 8)}…），等待 worker 执行`)
      await streamCrawlLogs(res.task_id)
    } catch (e) {
      setCurrentTask(null)
      setNotice(e instanceof ApiError ? `触发失败：${e.message}` : '触发失败，请检查后端与 Redis 队列')
    }
  }

  // 订阅爬虫实时日志 SSE（GET /admin/crawl/task/{taskId}/stream，逐行推送 scrapy 输出）
  async function streamCrawlLogs(taskId: string) {
    const ctrl = new AbortController()
    // 60s 无终态事件则中止（与后端 600s 兜底相比更保守，避免前端连接悬挂）
    const timer = setTimeout(() => ctrl.abort(), 60_000)
    try {
      // admin 端点需认证，fetch 不会自动附加 token（resume SSE 端点无认证故无此问题）
      const headers: Record<string, string> = {}
      const token = getAccessToken()
      if (token) headers.Authorization = `Bearer ${token}`
      const resp = await fetch(`/api/v1/admin/crawl/task/${taskId}/stream`, {
        signal: ctrl.signal,
        headers,
      })
      if (!resp.ok || !resp.body) throw new Error('SSE 连接失败')
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
            if (line) {
              setCurrentTask((t) =>
                t ? { ...t, logs: [...t.logs, line].slice(-200) } : t,
              )
            }
          } else if (event === 'progress') {
            setCurrentTask((t) => (t ? { ...t, status: 'running' } : t))
          } else if (event === 'done') {
            setNotice('爬取任务执行完成，数据已写入 output/*.jsonl')
            setCurrentTask((t) => (t ? { ...t, status: 'done', progress: 100 } : t))
            return
          } else if (event === 'error') {
            const msg = payload.message ?? payload.error ?? '任务执行失败'
            setNotice(`爬取任务失败：${msg}`)
            setCurrentTask((t) => (t ? { ...t, status: 'failed' } : t))
            return
          }
        }
      }
    } catch {
      // 超时/中断：SSE 结束，任务可能仍在后台执行
      setNotice('日志推送结束（任务可能在后台继续执行），可查看 output 文件确认结果')
    } finally {
      clearTimeout(timer)
    }
    setCurrentTask((t) =>
      t && t.status !== 'done' && t.status !== 'failed' ? { ...t, status: 'done' } : t,
    )
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
                          <Button size="sm" variant="ghost">日志</Button>
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
                <Select value={form.platform} onValueChange={(v) => setForm((f) => ({ ...f, platform: v }))}>
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
                  placeholder="高级前端"
                />
              </div>
              <div className="space-y-1.5">
                <Label>城市</Label>
                <Input
                  value={form.city}
                  onChange={(e) => setForm((f) => ({ ...f, city: e.target.value }))}
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
                        <Button size="sm" variant="ghost">详情</Button>
                      </TableCell>
                    </TableRow>
                  )
                })
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </>
  )
}
