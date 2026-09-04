/* eslint-disable react-refresh/only-export-components -- 导出趋势图数据常量供测试断言，HMR 粒度降级可接受 */
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import {
  Activity,
  Bot,
  ClipboardCheck,
  Database,
  Filter,
  GitFork,
  Globe,
  Network,
  RefreshCw,
  Settings2,
  Shield,
  Users,
  Workflow,
  Wrench,
} from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Reveal } from '@/components/ui/reveal'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { apiGet, apiPost } from '@/lib/api'
import { formatDateTime } from '@/lib/utils'
import type { components } from '@/types/api'

/* ------------------------------------------------------------------ */
/*  类型定义（对应后端 /admin/* 返回）                                    */
/* ------------------------------------------------------------------ */

interface AuditLogItem {
  id: number
  time: string
  type: AuditActionType
  operator: string
  detail: string
  ip: string
}

type AuditActionType = '用户管理' | '爬取' | '岗位审核' | '系统'

/** 后端 /admin/crawl/status 响应 data（契约 CrawlStatusData） */
type CrawlStatusData = components['schemas']['CrawlStatusData']

interface SourceItem {
  name: string
  level: string
  levelVariant: 'default' | 'outline' | 'emerging' | 'stable' | 'declining' | 'archived'
  status: 'normal' | 'delayed' | 'failed' | 'archived'
  /** /admin/crawl/status 返回的采集指标 */
  files: number
  totalCount: number
  todayCount: number
  lastRun: string | null
}

interface StatItem {
  label: string
  value: string
  delta: string
  icon: typeof Users
  deltaType: 'up' | 'down' | 'neutral'
}

const STATUS_LABEL: Record<SourceItem['status'], string> = {
  normal: '正常',
  delayed: '延迟',
  failed: '失败',
  archived: '归档',
}

const STATUS_DOT_CLASS: Record<SourceItem['status'], string> = {
  normal: 'bg-state-emerging',
  delayed: 'bg-state-declining',
  failed: 'bg-state-archived',
  archived: 'bg-ink-faint',
}

const AUDIT_TYPE_VARIANT: Record<AuditActionType, 'default' | 'outline' | 'emerging' | 'stable'> = {
  '用户管理': 'default',
  '爬取': 'outline',
  '岗位审核': 'emerging',
  '系统': 'stable',
}

/** action → 审计日志类型 */
function actionType(action: string): AuditActionType {
  if (action.startsWith('admin.user')) return '用户管理'
  if (action.startsWith('crawl') || action.startsWith('etl')) return '爬取'
  if (action.startsWith('review')) return '岗位审核'
  return '系统'
}

export const QUICK_ACTIONS: {
  id: string
  label: string
  icon: typeof RefreshCw
  desc: string
  /** 导航型快捷入口（to 存在时渲染 Link，否则为触发型按钮） */
  to?: string
}[] = [
  { id: 'crawl', label: '触发全量爬取', icon: RefreshCw, desc: '重新采集所有数据源' },
  { id: 'etl-clean', label: '数据清洗', icon: Filter, desc: 'SimHash 近似去重 · 立即执行' },
  { id: 'etl-graph', label: '聚合入图', icon: Network, desc: '岗位-技能图写回 Neo4j' },
  { id: 'etl-full', label: '完整 ETL 管线', icon: Workflow, desc: '采集→清洗→入图→快照全阶段' },
  { id: 'goto-review', label: '前往岗位审核', icon: ClipboardCheck, desc: '处理候选晋升 / 驳回', to: '/admin/review' },
  { id: 'goto-dictguard', label: '字典守卫', icon: Shield, desc: '技能/岗位/课程 LLM 自动治理 · 手动巡检', to: '/admin/review?tab=dict' },
  { id: 'goto-crawl', label: '爬取管理', icon: Globe, desc: '单源触发 · 任务状态 · 输出查看', to: '/admin/crawl' },
  { id: 'goto-llm', label: 'LLM 配置', icon: Bot, desc: '多 Provider 重试链 · 健康检查', to: '/admin/llm' },
  { id: 'goto-llm-decisions', label: 'LLM 决策', icon: Bot, desc: '六域决策透明 · 验收·提案·拦截', to: '/admin/llm-decisions' },
  { id: 'goto-settings', label: '系统配置', icon: Settings2, desc: '运行时参数 · 重启生效', to: '/admin/settings/tasks' },
  { id: 'goto-dictguard-settings', label: '系统节流', icon: Shield, desc: '演化缓存 + 驳回冷却期 · 重启生效', to: '/admin/settings/system' },
  { id: 'goto-users', label: '用户管理', icon: Users, desc: '账号 · 角色 · 状态', to: '/admin/users' },
  { id: 'goto-lineage', label: '数据血缘', icon: GitFork, desc: '岗位-技能-课程链路溯源', to: '/admin/lineage' },
  { id: 'goto-raw', label: '原始数据', icon: Database, desc: 'JD/课程/论文/社区信号四类 raw 治理', to: '/admin/raw' },
  { id: 'goto-skills', label: '技能治理', icon: Wrench, desc: '白名单∪别名总览 · 别名复核', to: '/admin/skills' },
]

/** ETL 触发型快捷操作 → 后端白名单 job（契约 POST /admin/etl/trigger） */
export const ETL_ACTION_JOBS: Record<string, string> = {
  'etl-clean': 'dedup_simhash',
  'etl-graph': 'aggregate_positions',
  'etl-full': 'run_etl_pipeline',
}

const LEVEL_VARIANT: Record<string, SourceItem['levelVariant']> = {
  A: 'default',
  B: 'outline',
  C: 'emerging',
  信号: 'stable',
  论文: 'declining',
  课程: 'emerging',
}

/** ISO 时间 → 紧凑 MM-dd HH:mm（窄卡片避免长日期撑破布局） */
function formatRunTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/* ------------------------------------------------------------------ */
/*  AdminDashboardPage                                                  */
/* ------------------------------------------------------------------ */

export function AdminDashboardPage() {
  const [runningActions, setRunningActions] = useState<Set<string>>(new Set())
  const [actionMessages, setActionMessages] = useState<Map<string, string>>(new Map())
  const [stats, setStats] = useState<StatItem[]>([])
  const [sources, setSources] = useState<SourceItem[]>([])
  const [auditLogs, setAuditLogs] = useState<AuditLogItem[]>([])
  const [auditQueueCount, setAuditQueueCount] = useState(0)

  // 加载真实管理数据（users / crawl / audit / positions）
  useEffect(() => {
    let cancelled = false
    Promise.allSettled([
      apiGet<{ items: { id: string }[]; total: number }>('/admin/users?page=1&size=1'),
      apiGet<CrawlStatusData>('/admin/crawl/status'),
      apiGet<components['schemas']['AuditLogsData']>('/admin/audit/logs?page=1&size=10'),
      apiGet<{ items: unknown[]; total: number }>('/admin/positions/pending'),
    ]).then(([usersRes, crawlRes, auditRes, pendingRes]) => {
      if (cancelled) return

      const userTotal = usersRes.status === 'fulfilled' ? usersRes.value.total : 0
      const platforms = crawlRes.status === 'fulfilled' ? crawlRes.value.platforms : []
      const rawJd = crawlRes.status === 'fulfilled' ? crawlRes.value.metrics.raw.jd : 0
      const logs = auditRes.status === 'fulfilled' ? auditRes.value.items : []
      const pending = pendingRes.status === 'fulfilled' ? pendingRes.value.total : 0

      setStats([
        { label: '总注册用户数', value: userTotal.toLocaleString(), delta: 'users 表', icon: Users, deltaType: 'neutral' },
        { label: '已入库原始数据', value: rawJd.toLocaleString(), delta: '爬虫输出', icon: Database, deltaType: 'neutral' },
        { label: '待审核岗位', value: pending.toLocaleString(), delta: '—', icon: Shield, deltaType: 'neutral' },
        { label: '采集数据源', value: platforms.length.toLocaleString(), delta: '13 源', icon: Activity, deltaType: 'neutral' },
      ])
      setSources(
        platforms.map((p) => ({
          name: p.name,
          level: p.level,
          levelVariant: LEVEL_VARIANT[p.level] ?? 'outline',
          // 状态由真实采集数据派生：今日有产出→正常 / 有历史无今日→延迟 / 无记录→归档
          status: p.today_count > 0 ? 'normal' : p.total_count > 0 ? 'delayed' : 'archived',
          files: p.files,
          totalCount: p.total_count,
          todayCount: p.today_count,
          lastRun: p.last_run ?? null,
        })),
      )
      setAuditLogs(
        logs.map((l) => ({
          id: l.id,
          time: formatDateTime(l.created_at),
          type: actionType(l.action),
          operator: (l.detail?.username as string | undefined) ?? l.user_id,
          detail: `${l.action} · ${l.resource}`,
          ip: l.ip_address || '—',
        })),
      )
      setAuditQueueCount(pending)
    })
    return () => {
      cancelled = true
    }
  }, [])

  // 轮询中止标志 + 提示定时器登记（第八轮 P1-13：路由切走后停止 ETL 空轮询、
  // 清理 flashAction 定时器，避免卸载后 setState——同 resume-match-page pollCancelledRef 范式）
  const pollCancelledRef = useRef(false)
  const flashTimersRef = useRef(new Set<ReturnType<typeof setTimeout>>())
  useEffect(
    () => () => {
      pollCancelledRef.current = true
      flashTimersRef.current.forEach((t) => clearTimeout(t))
      flashTimersRef.current.clear()
    },
    [],
  )

  /** 按钮下方一次性提示（ttlMs 后自动清除；0 = 常驻直至下次更新） */
  function flashAction(id: string, text: string, ttlMs = 4000) {
    setActionMessages((prev) => new Map(prev).set(id, text))
    if (ttlMs > 0) {
      const timer = setTimeout(() => {
        flashTimersRef.current.delete(timer)
        setActionMessages((prev) => {
          const next = new Map(prev)
          next.delete(id)
          return next
        })
      }, ttlMs)
      flashTimersRef.current.add(timer)
    }
  }

  /** ETL 任务状态轮询：3s 间隔至终态（完整管线最长窗口 3h，400 次 ≈ 20min
   * 后不再阻塞提示，任务仍在后台由 worker 继续执行） */
  async function trackEtlTask(id: string, taskId: string) {
    for (let i = 0; i < 400; i++) {
      await new Promise((r) => setTimeout(r, 3000))
      if (pollCancelledRef.current) return
      try {
        const t = await apiGet<components['schemas']['EtlTaskStatus']>(
          `/admin/etl/task/${taskId}`,
        )
        if (t.status === 'success') return flashAction(id, '执行完成')
        if (t.status === 'failed') return flashAction(id, '执行失败（详情见服务端日志）')
      } catch {
        return flashAction(id, '状态查询中断，请稍后刷新查看')
      }
    }
    flashAction(id, '仍在执行，请稍后刷新查看')
  }

  async function handleQuickAction(id: string) {
    if (runningActions.has(id)) return
    setRunningActions((prev) => new Set(prev).add(id))
    setActionMessages((prev) => {
      const next = new Map(prev)
      next.delete(id)
      return next
    })
    try {
      const job = ETL_ACTION_JOBS[id]
      if (job) {
        // ETL 触发型（数据清洗/聚合入图/完整管线）：入队即释放按钮，
        // 由 trackEtlTask 轮询终态更新提示——管线可长达小时级，不占用按钮态
        const res = await apiPost<components['schemas']['EtlTriggerResult']>('/admin/etl/trigger', { job })
        flashAction(id, '已入队 · 执行中…', 0)
        void trackEtlTask(id, res.task_id)
        return
      }
      // 真实触发：对每个平台入队 crawl_platform 任务（POST /admin/crawl/trigger）
      // 不传 keyword：留空走平台热度/最新采集（08-16 起爬虫无默认关键词，契约 keyword 可选）
      const res = await apiGet<CrawlStatusData>('/admin/crawl/status')
      for (const p of res.platforms) {
        await apiPost('/admin/crawl/trigger', { platform: p.id })
      }
      flashAction(id, `已入队 ${res.platforms.length} 个平台`)
    } catch {
      flashAction(id, '触发失败')
    } finally {
      setRunningActions((prev) => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
    }
  }

  return (
    <>
      <PageHeader
        title="管理后台"
        description="管理统一入口 · 系统总览 · 源状态 · 审核队列 · 审计日志 · 全部管理功能快捷导航"
      />

      {/* 顶部概述指标卡（真实数据派生） */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        {stats.map((stat, i) => {
          const Icon = stat.icon
          const deltaColor =
            stat.deltaType === 'up'
              ? 'text-state-emerging'
              : stat.deltaType === 'down'
                ? 'text-state-declining'
                : 'text-ink-muted'
          return (
            <Reveal key={stat.label} delay={i * 90} className="h-full">
              <Card className="h-full">
                <CardContent className="py-4">
                  <div className="flex items-center justify-between mb-2">
                    <Icon className="size-4 text-ink-faint" />
                    <span className={`text-xs font-mono ${deltaColor}`}>{stat.delta}</span>
                  </div>
                  <div className="text-2xl font-semibold tracking-tight tabular-nums">{stat.value}</div>
                  <div className="text-xs text-ink-muted mt-1">{stat.label}</div>
                </CardContent>
              </Card>
            </Reveal>
          )
        })}
      </div>

      {/* 数据源运行状态网格（真实 /admin/crawl/status） */}
      <Reveal delay={380}>
        <Card className="mb-6">
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Database className="size-4" />
            <span>数据源运行状态</span>
            <Badge variant="outline" className="text-xs ml-auto font-mono">A/B/C 分级</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-2 text-xs">
            {sources.map((src) => (
              <div
                key={src.name}
                className={`rounded-md border border-border p-2.5 ${
                  src.status === 'archived' ? 'opacity-50' : ''
                }`}
              >
                <div className="flex items-center justify-between mb-1.5">
                  <Badge variant={src.levelVariant} className="text-[11px] leading-none px-1.5 py-0">
                    {src.level}
                  </Badge>
                  <span className="flex items-center gap-1">
                    <span className={`size-1.5 rounded-full ${STATUS_DOT_CLASS[src.status]}`} />
                    <span className="text-[10px] text-ink-faint">{STATUS_LABEL[src.status]}</span>
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded border border-border bg-subtle text-[12px] font-semibold text-ink-muted">
                    {src.name.charAt(0)}
                  </span>
                  <span className="text-ink truncate font-medium">{src.name}</span>
                </div>
                {/* 采集指标：累计 / 今日 / 文件数 / 最近运行 */}
                <div className="mt-2 space-y-0.5 text-[11px] text-ink-faint tabular-nums">
                  <div>
                    累计 {src.totalCount.toLocaleString()}
                    {src.todayCount > 0 && <span className="text-state-emerging"> · 今日 +{src.todayCount}</span>}
                  </div>
                  <div>
                    文件 {src.files}
                    <span className="ml-1">{src.lastRun ? `最近 ${formatRunTime(src.lastRun)}` : '· 未采集'}</span>
                  </div>
                </div>
              </div>
            ))}
            {sources.length === 0 && (
              <p className="col-span-7 py-6 text-center text-ink-faint">暂无采集记录</p>
            )}
          </div>
        </CardContent>
      </Card>
      </Reveal>

      {/* 审核队列 + 审计日志 两列布局 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
        {/* 审核队列摘要（/admin/positions/pending，LLM 信号未上线为空） */}
        <Reveal delay={500} className="h-full">
          <Card className="h-full">
            <CardHeader>
              <CardTitle className="text-sm flex items-center justify-between">
                <span>审核队列摘要</span>
                <span className="text-xs font-normal text-ink-faint">{auditQueueCount} 条待处理</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {auditQueueCount === 0 ? (
                <div className="py-12 text-center text-sm text-ink-faint">
                  暂无待审核岗位 · 新岗位信号由 LLM 抽取 + 发现检测器产出（M3/M4 交付）
                  <div className="mt-2">
                    <Button size="sm" variant="outline" asChild>
                      <Link to="/admin/review">前往审核页</Link>
                    </Button>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-ink-muted py-8 text-center">{auditQueueCount} 条待审核，前往审核页处理</p>
              )}
            </CardContent>
          </Card>
        </Reveal>

        {/* 系统审计日志（真实 /admin/audit/logs） */}
        <Reveal delay={620} className="h-full">
          <Card className="h-full">
            <CardHeader>
              <CardTitle className="text-sm flex items-center justify-between">
                <span>系统审计日志</span>
                <span className="text-xs font-normal text-ink-faint">最近 {auditLogs.length} 条</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {auditLogs.length === 0 ? (
                <p className="py-12 text-center text-sm text-ink-faint">
                  暂无审计日志 · 登录 / 管理操作将写入 audit_logs 表
                </p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>时间</TableHead>
                      <TableHead>操作类型</TableHead>
                      <TableHead>操作人</TableHead>
                      <TableHead>详情</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {auditLogs.slice(0, 5).map((log) => (
                      <TableRow key={log.id}>
                        <TableCell className="font-mono text-xs text-ink-muted whitespace-nowrap">{log.time}</TableCell>
                        <TableCell>
                          <Badge variant={AUDIT_TYPE_VARIANT[log.type]} className="text-[11px] leading-none px-1.5 py-0">
                            {log.type}
                          </Badge>
                        </TableCell>
                        <TableCell className="font-mono text-xs text-ink-secondary">{log.operator}</TableCell>
                        <TableCell className="text-xs text-ink max-w-[200px] truncate" title={log.detail}>
                          {log.detail}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </Reveal>
      </div>

      {/* 快捷操作区：触发型（爬取）+ 导航型（审核/爬取管理/LLM/用户） */}
      <Reveal delay={740}>
        <Card>
        <CardHeader>
          <CardTitle className="text-sm">快捷操作</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {QUICK_ACTIONS.map((action) => {
              const Icon = action.icon
              const isRunning = runningActions.has(action.id)
              const message = actionMessages.get(action.id)
              const inner = (
                <>
                  <div className="flex items-center gap-2 w-full">
                    <Icon className={`size-4 shrink-0 ${isRunning ? 'animate-spin' : ''}`} />
                    <span className="text-sm font-medium">{action.label}</span>
                    {/* 审核入口显示真实待审核数（/admin/positions/pending total） */}
                    {action.id === 'goto-review' && auditQueueCount > 0 && (
                      <span className="ml-auto rounded-full bg-state-candidate/15 px-2 py-0.5 text-[11px] font-medium text-state-candidate">
                        {auditQueueCount}
                      </span>
                    )}
                    {message && (
                      <span className="ml-auto text-[11px] text-state-emerging font-medium">{message}</span>
                    )}
                  </div>
                  <span className="text-[12px] text-ink-muted font-normal">{action.desc}</span>
                </>
              )
              return action.to ? (
                <Button key={action.id} variant="outline" asChild className="h-auto flex-col items-start gap-2 p-4 text-left">
                  <Link to={action.to}>{inner}</Link>
                </Button>
              ) : (
                <Button
                  key={action.id}
                  variant="outline"
                  disabled={isRunning}
                  onClick={() => handleQuickAction(action.id)}
                  className="h-auto flex-col items-start gap-2 p-4 text-left"
                >
                  {inner}
                </Button>
              )
            })}
          </div>
        </CardContent>
      </Card>
      </Reveal>
    </>
  )
}
