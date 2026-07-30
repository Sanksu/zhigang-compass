import { useState } from 'react'
import { Activity, AlertCircle, Database, Gauge } from 'lucide-react'
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

/** 爬虫运行状态 — 对应 §4 平台采集生命周期 */
type CrawlStatus = 'running' | 'idle' | 'failed' | 'archived'

/** 平台分级 — 对齐设计文档 §4 的 A/B/C + 信号 + 课程 */
type PlatformLevel = 'A' | 'B' | 'C' | '信号' | '课程'

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
  time: string
  platform: string
  keyword: string
  count: number
  status: 'success' | 'partial' | 'failed'
  duration: string
}

interface CurrentTask {
  platform: string
  keyword: string
  city: string
  maxPages: number
  status: 'queued' | 'running' | 'done'
  progress: number
  collected: number
  total: number
}

/** 顶部指标 — P0 标准要求 ≥100 条/日，故今日采集量是核心健康度 */
const MOCK_METRICS = [
  { id: 'today', label: '今日采集量', value: '1,284', delta: '+412', deltaColor: 'text-state-emerging', icon: Database, hint: '国内 826 · 国际 458' },
  { id: 'active', label: '活跃爬虫数', value: '8', delta: '+2', deltaColor: 'text-state-emerging', icon: Activity, hint: '13 源在线运行' },
  { id: 'failed', label: '失败任务数', value: '3', delta: '-1', deltaColor: 'text-state-declining', icon: AlertCircle, hint: 'LinkedIn 限频触发' },
  { id: 'rate', label: '平均采集速率', value: '2.4/s', delta: '+0.3', deltaColor: 'text-state-emerging', icon: Gauge, hint: '近 1 小时滑动均值' },
]

/**
 * 13 源在线 + 拉勾归档 = 14 行
 * 与仪表盘「14 源数据底座」对齐，归档源保留以体现历史可追溯
 */
const MOCK_PLATFORMS: PlatformRow[] = [
  { id: 'boss', name: 'BOSS直聘', level: 'A', status: 'running', todayCount: 412, totalCount: 1842, lastRun: '07-29 14:32' },
  { id: 'zhaopin', name: '智联招聘', level: 'A', status: 'idle', todayCount: 86, totalCount: 1456, lastRun: '07-29 13:15' },
  { id: 'monster', name: 'Monster', level: 'A', status: 'idle', todayCount: 42, totalCount: 982, lastRun: '07-29 10:20' },
  { id: 'indeed', name: 'Indeed', level: 'A', status: 'running', todayCount: 124, totalCount: 1124, lastRun: '07-29 14:30' },
  { id: 'lagou', name: '拉勾网', level: 'B', status: 'archived', todayCount: 0, totalCount: 0, lastRun: '2024-03-15 归档' },
  { id: 'glassdoor', name: 'Glassdoor', level: 'B', status: 'idle', todayCount: 28, totalCount: 642, lastRun: '07-29 09:10' },
  { id: 'linkedin', name: 'LinkedIn', level: 'B', status: 'failed', todayCount: 0, totalCount: 478, lastRun: '07-29 08:45' },
  { id: 'maimai', name: '脉脉', level: 'C', status: 'idle', todayCount: 0, totalCount: 312, lastRun: '07-29 06:00' },
  { id: 'github', name: 'GitHub', level: '信号', status: 'running', todayCount: 56, totalCount: 248, lastRun: '07-29 14:35' },
  { id: 'stackoverflow', name: 'Stack Overflow', level: '信号', status: 'idle', todayCount: 18, totalCount: 186, lastRun: '07-29 12:00' },
  { id: 'arxiv', name: 'arXiv', level: '信号', status: 'idle', todayCount: 8, totalCount: 94, lastRun: '07-29 05:30' },
  { id: 'mooc', name: '中国大学MOOC', level: '课程', status: 'idle', todayCount: 12, totalCount: 168, lastRun: '07-29 04:00' },
  { id: 'coursera', name: 'Coursera', level: '课程', status: 'idle', todayCount: 6, totalCount: 142, lastRun: '07-29 04:15' },
  { id: 'edx', name: 'edX', level: '课程', status: 'idle', todayCount: 4, totalCount: 76, lastRun: '07-29 04:30' },
]

/** 最近 10 条爬取历史 */
const MOCK_HISTORY: HistoryRow[] = [
  { id: 'h1', time: '07-29 14:32', platform: 'BOSS直聘', keyword: '高级前端', count: 412, status: 'success', duration: '22min' },
  { id: 'h2', time: '07-29 14:30', platform: 'Indeed', keyword: 'Senior SDE', count: 124, status: 'success', duration: '8min' },
  { id: 'h3', time: '07-29 13:18', platform: 'BOSS直聘', keyword: 'Java 后端', count: 356, status: 'success', duration: '19min' },
  { id: 'h4', time: '07-29 12:05', platform: 'LinkedIn', keyword: 'Product Manager', count: 0, status: 'failed', duration: '2min' },
  { id: 'h5', time: '07-29 10:47', platform: '智联招聘', keyword: '数据分析师', count: 86, status: 'partial', duration: '12min' },
  { id: 'h6', time: '07-29 09:15', platform: '脉脉', keyword: '算法工程师', count: 42, status: 'success', duration: '15min' },
  { id: 'h7', time: '07-29 05:00', platform: 'arXiv', keyword: 'LLM', count: 8, status: 'success', duration: '6min' },
  { id: 'h8', time: '07-29 04:30', platform: 'edX', keyword: '课程同步', count: 4, status: 'success', duration: '3min' },
  { id: 'h9', time: '07-29 04:15', platform: 'Coursera', keyword: '课程同步', count: 6, status: 'success', duration: '4min' },
  { id: 'h10', time: '07-29 04:00', platform: '中国大学MOOC', keyword: '课程同步', count: 12, status: 'success', duration: '5min' },
]

const STATUS_META: Record<CrawlStatus, { variant: 'stable' | 'candidate' | 'archived'; label: string }> = {
  running: { variant: 'stable', label: '运行中' },
  idle: { variant: 'candidate', label: '空闲' },
  failed: { variant: 'archived', label: '失败' },
  archived: { variant: 'archived', label: '归档' },
}

const HISTORY_STATUS_META: Record<HistoryRow['status'], { variant: 'stable' | 'declining' | 'archived'; label: string }> = {
  success: { variant: 'stable', label: '成功' },
  partial: { variant: 'declining', label: '部分' },
  failed: { variant: 'archived', label: '失败' },
}

/** 等级配色 — 与 Badge outline 叠加，A 绿/B 蓝/C 橙突出分级语义 */
const LEVEL_CLASS: Record<PlatformLevel, string> = {
  A: 'text-state-emerging border-state-emerging/30',
  B: 'text-state-stable border-state-stable/30',
  C: 'text-state-declining border-state-declining/30',
  '信号': 'text-ink-secondary border-border-strong',
  '课程': 'text-ink-muted border-border',
}

export function AdminCrawlPage() {
  const [form, setForm] = useState({
    platform: 'BOSS直聘',
    keyword: '',
    city: '北京',
    maxPages: 30,
  })
  const [currentTask, setCurrentTask] = useState<CurrentTask | null>(null)

  // 任务未完成时禁用所有触发入口，避免并发任务导致调度混乱
  const isBusy = currentTask !== null && currentTask.status !== 'done'

  function triggerCrawl(platform: string) {
    if (isBusy) return
    const task: CurrentTask = {
      platform,
      keyword: form.keyword || '高级前端',
      city: form.city || '北京',
      maxPages: form.maxPages || 30,
      status: 'queued',
      progress: 0,
      collected: 0,
      total: 300,
    }
    setCurrentTask(task)
    // 模拟调度延迟：队列中 3s 后进入运行态并展示 mock 进度
    setTimeout(() => {
      setCurrentTask((t) => (t ? { ...t, status: 'running', progress: 60, collected: 184 } : t))
    }, 3000)
  }

  return (
    <>
      <PageHeader title="爬取管理" description="手动触发 13 源采集 · 进度监控 · 历史回溯" />

      {/* 顶部指标卡 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {MOCK_METRICS.map((m) => {
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
            <span className="text-xs font-normal text-ink-faint">13 源在线 · 拉勾归档</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
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
              {MOCK_PLATFORMS.map((p) => {
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
                          onClick={() => triggerCrawl(p.name)}
                        >
                          触发
                        </Button>
                        <Button size="sm" variant="ghost">日志</Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
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
                    {MOCK_PLATFORMS.filter((p) => p.status !== 'archived').map((p) => (
                      <SelectItem key={p.id} value={p.name}>{p.name}</SelectItem>
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
                <Badge variant={currentTask.status === 'running' ? 'stable' : 'candidate'}>
                  {currentTask.status === 'queued' ? '队列中' : currentTask.status === 'running' ? '运行中' : '完成'}
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
                      className="h-full bg-state-stable transition-all duration-500"
                      style={{ width: `${currentTask.progress}%` }}
                    />
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
            <span className="text-xs font-normal text-ink-faint">最近 10 条</span>
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
                <TableHead className="text-right">耗时</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {MOCK_HISTORY.map((h) => {
                const meta = HISTORY_STATUS_META[h.status]
                return (
                  <TableRow key={h.id}>
                    <TableCell className="text-xs font-mono text-ink-muted">{h.time}</TableCell>
                    <TableCell className="font-medium">{h.platform}</TableCell>
                    <TableCell className="text-ink-secondary">{h.keyword}</TableCell>
                    <TableCell className="text-right tabular-nums font-mono">{h.count}</TableCell>
                    <TableCell><Badge variant={meta.variant}>{meta.label}</Badge></TableCell>
                    <TableCell className="text-right text-xs font-mono text-ink-muted">{h.duration}</TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" variant="ghost">详情</Button>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </>
  )
}
