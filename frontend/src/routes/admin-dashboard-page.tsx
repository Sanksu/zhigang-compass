import { useState } from 'react'
import { Link } from 'react-router'
import {
  Activity,
  Clock,
  Database,
  FileText,
  RefreshCw,
  Shield,
  Trash2,
  Upload,
  Users,
  Zap,
} from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
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

/* ------------------------------------------------------------------ */
/*  Mock 数据                                                          */
/* ------------------------------------------------------------------ */

const MOCK_STATS = [
  { label: '总注册用户数', value: '1,248', delta: '+38', icon: Users, deltaType: 'up' as const },
  { label: '今日活跃用户', value: '342', delta: '+12%', icon: Activity, deltaType: 'up' as const },
  { label: '待审核岗位', value: '8', delta: '-3', icon: Shield, deltaType: 'down' as const },
  { label: '待处理任务数', value: '5', delta: '+2', icon: FileText, deltaType: 'up' as const },
  { label: '今日采集量', value: '6,842', delta: '+412', icon: Database, deltaType: 'up' as const },
  { label: '系统运行天数', value: '137', delta: '—', icon: Clock, deltaType: 'neutral' as const },
]

interface SourceItem {
  name: string
  level: string
  levelVariant: 'default' | 'outline' | 'emerging' | 'stable' | 'declining' | 'archived'
  status: 'normal' | 'delayed' | 'failed' | 'archived'
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

const MOCK_SOURCES: SourceItem[] = [
  { name: 'BOSS直聘', level: 'A', levelVariant: 'default', status: 'normal' },
  { name: '智联招聘', level: 'A', levelVariant: 'default', status: 'normal' },
  { name: 'Monster', level: 'A', levelVariant: 'default', status: 'normal' },
  { name: 'Indeed', level: 'A', levelVariant: 'default', status: 'normal' },
  { name: 'Glassdoor', level: 'B', levelVariant: 'outline', status: 'normal' },
  { name: 'LinkedIn', level: 'B', levelVariant: 'outline', status: 'delayed' },
  { name: '脉脉', level: 'C', levelVariant: 'emerging', status: 'normal' },
  { name: 'GitHub', level: '信号', levelVariant: 'stable', status: 'normal' },
  { name: 'StackOverflow', level: '信号', levelVariant: 'stable', status: 'normal' },
  { name: 'arXiv', level: '论文', levelVariant: 'declining', status: 'normal' },
  { name: '中国大学MOOC', level: '课程', levelVariant: 'emerging', status: 'normal' },
  { name: 'Coursera', level: '课程', levelVariant: 'emerging', status: 'normal' },
  { name: 'edX', level: '课程', levelVariant: 'emerging', status: 'normal' },
]

interface AuditQueueItem {
  id: string
  name: string
  source: string
  confidence: number
  foundAt: string
}

const MOCK_AUDIT_QUEUE: AuditQueueItem[] = [
  { id: 'r1', name: '大模型应用工程师', source: 'BOSS直聘', confidence: 0.86, foundAt: '07-29 14:20' },
  { id: 'r2', name: 'AI Agent 开发工程师', source: 'GitHub', confidence: 0.78, foundAt: '07-29 13:50' },
  { id: 'r3', name: '数据合规官', source: '智联招聘', confidence: 0.71, foundAt: '07-29 12:30' },
  { id: 'r4', name: '提示词工程师', source: 'Indeed', confidence: 0.83, foundAt: '07-29 11:15' },
  { id: 'r5', name: 'MLOps 工程师', source: 'StackOverflow', confidence: 0.75, foundAt: '07-29 10:40' },
]

type AuditActionType = '用户管理' | '爬取' | '岗位审核' | '系统'

interface AuditLogItem {
  id: string
  time: string
  type: AuditActionType
  operator: string
  detail: string
  ip: string
}

const AUDIT_TYPE_VARIANT: Record<AuditActionType, 'default' | 'outline' | 'emerging' | 'stable'> = {
  '用户管理': 'default',
  '爬取': 'outline',
  '岗位审核': 'emerging',
  '系统': 'stable',
}

const MOCK_AUDIT_LOGS: AuditLogItem[] = [
  { id: 'l1', time: '14:32:18', type: '用户管理', operator: 'admin_zhang', detail: '修改用户 user_chen 角色：user → guest', ip: '192.168.1.100' },
  { id: 'l2', time: '14:20:05', type: '爬取', operator: 'system', detail: 'BOSS直聘 增量爬取完成，新增 412 条 JD', ip: '10.0.0.5' },
  { id: 'l3', time: '13:45:32', type: '岗位审核', operator: 'admin_li', detail: '驳回岗位「Web3 架构师」为 rejected', ip: '192.168.1.101' },
  { id: 'l4', time: '12:10:11', type: '系统', operator: 'system', detail: '每日 ETL 聚合调度执行成功，耗时 48min', ip: '10.0.0.2' },
  { id: 'l5', time: '11:30:44', type: '岗位审核', operator: 'admin_zhang', detail: '批准岗位「风控建模工程师」为 emerging', ip: '192.168.1.100' },
  { id: 'l6', time: '10:15:27', type: '用户管理', operator: 'admin_wang', detail: '禁用用户 user_zhao（违规操作）', ip: '192.168.1.102' },
  { id: 'l7', time: '09:50:03', type: '爬取', operator: 'system', detail: 'GitHub 信号爬取完成，检测到 248 个 langchain-agent 项目', ip: '10.0.0.5' },
  { id: 'l8', time: '08:30:58', type: '系统', operator: 'admin_zhang', detail: '手动触发全量爬取任务', ip: '192.168.1.100' },
]

const CONFIDENCE_TONE = (c: number) =>
  c >= 0.8 ? 'text-state-emerging' : c >= 0.7 ? 'text-state-stable' : 'text-state-declining'

const QUICK_ACTIONS = [
  { id: 'crawl', label: '触发全量爬取', icon: RefreshCw, desc: '重新采集所有 14 个数据源' },
  { id: 'etl', label: '执行 ETL 聚合', icon: Zap, desc: '清洗 → 去重 → 结构化 → 图谱同步' },
  { id: 'cache', label: '清理缓存', icon: Trash2, desc: '清除查询缓存与临时计算结果' },
  { id: 'report', label: '导出系统报告', icon: Upload, desc: '生成系统运营状态综合报告' },
]

/* ------------------------------------------------------------------ */
/*  AdminDashboardPage                                                  */
/* ------------------------------------------------------------------ */

export function AdminDashboardPage() {
  const [runningActions, setRunningActions] = useState<Set<string>>(new Set())
  const [actionMessages, setActionMessages] = useState<Map<string, string>>(new Map())

  function handleQuickAction(id: string) {
    if (runningActions.has(id)) return
    setRunningActions((prev) => new Set(prev).add(id))
    setActionMessages((prev) => {
      const next = new Map(prev)
      next.delete(id)
      return next
    })
    setTimeout(() => {
      setRunningActions((prev) => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
      setActionMessages((prev) => new Map(prev).set(id, '已触发'))
      setTimeout(() => {
        setActionMessages((prev) => {
          const next = new Map(prev)
          next.delete(id)
          return next
        })
      }, 4000)
    }, 2000)
  }

  return (
    <>
      <PageHeader
        title="管理后台"
        description="系统总览 · 源状态 · 审核队列 · 审计日志 · 快捷操作"
      />

      {/* 顶部概述指标卡 */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
        {MOCK_STATS.map((stat) => {
          const Icon = stat.icon
          const deltaColor =
            stat.deltaType === 'up'
              ? 'text-state-emerging'
              : stat.deltaType === 'down'
                ? 'text-state-declining'
                : 'text-ink-muted'
          return (
            <Card key={stat.label}>
              <CardContent className="py-4">
                <div className="flex items-center justify-between mb-2">
                  <Icon className="size-4 text-ink-faint" />
                  <span className={`text-xs font-mono ${deltaColor}`}>{stat.delta}</span>
                </div>
                <div className="text-2xl font-semibold tracking-tight tabular-nums">{stat.value}</div>
                <div className="text-xs text-ink-muted mt-1">{stat.label}</div>
              </CardContent>
            </Card>
          )
        })}
      </div>

      {/* 14 源运行状态网格 */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Database className="size-4" />
            <span>14 源运行状态</span>
            <Badge variant="outline" className="text-xs ml-auto font-mono">A/B/C 分级</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-2 text-xs">
            {MOCK_SOURCES.map((src) => (
              <div
                key={src.name}
                className={`rounded-md border border-border p-2.5 ${
                  src.status === 'archived' ? 'opacity-50' : ''
                }`}
              >
                <div className="flex items-center justify-between mb-1.5">
                  <Badge variant={src.levelVariant} className="text-[10px] leading-none px-1.5 py-0">
                    {src.level}
                  </Badge>
                  <span className="flex items-center gap-1">
                    <span className={`size-1.5 rounded-full ${STATUS_DOT_CLASS[src.status]}`} />
                    <span className="text-[9px] text-ink-faint">{STATUS_LABEL[src.status]}</span>
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded border border-border bg-subtle text-[11px] font-semibold text-ink-muted">
                    {src.name.charAt(0)}
                  </span>
                  <span className="text-ink truncate font-medium">{src.name}</span>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* 审核队列 + 审计日志 两列布局 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
        {/* 审核队列摘要 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm flex items-center justify-between">
              <span>审核队列摘要</span>
              <span className="text-xs font-normal text-ink-faint">最近 5 条</span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>岗位名</TableHead>
                  <TableHead>来源</TableHead>
                  <TableHead>置信度</TableHead>
                  <TableHead>发现时间</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {MOCK_AUDIT_QUEUE.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="font-medium">{item.name}</TableCell>
                    <TableCell className="text-ink-secondary">{item.source}</TableCell>
                    <TableCell>
                      <span className={`font-mono tabular-nums text-sm ${CONFIDENCE_TONE(item.confidence)}`}>
                        {Math.round(item.confidence * 100)}%
                      </span>
                    </TableCell>
                    <TableCell className="text-xs font-mono text-ink-muted">{item.foundAt}</TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" variant="ghost" asChild>
                        <Link to="/admin/review">前往审核</Link>
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        {/* 系统审计日志摘要 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm flex items-center justify-between">
              <span>系统审计日志</span>
              <span className="text-xs font-normal text-ink-faint">最近 5 条</span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>时间</TableHead>
                  <TableHead>操作类型</TableHead>
                  <TableHead>操作人</TableHead>
                  <TableHead>详情</TableHead>
                  <TableHead>IP</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {MOCK_AUDIT_LOGS.slice(0, 5).map((log) => (
                  <TableRow key={log.id}>
                    <TableCell className="font-mono text-xs text-ink-muted whitespace-nowrap">{log.time}</TableCell>
                    <TableCell>
                      <Badge variant={AUDIT_TYPE_VARIANT[log.type]} className="text-[10px] leading-none px-1.5 py-0">
                        {log.type}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs text-ink-secondary">{log.operator}</TableCell>
                    <TableCell className="text-xs text-ink max-w-[200px] truncate" title={log.detail}>
                      {log.detail}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-ink-faint">{log.ip}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>

      {/* 快捷操作区 */}
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
              return (
                <Button
                  key={action.id}
                  variant="outline"
                  disabled={isRunning}
                  onClick={() => handleQuickAction(action.id)}
                  className="h-auto flex-col items-start gap-2 p-4 text-left"
                >
                  <div className="flex items-center gap-2 w-full">
                    <Icon className={`size-4 shrink-0 ${isRunning ? 'animate-spin' : ''}`} />
                    <span className="text-sm font-medium">{action.label}</span>
                    {message && (
                      <span className="ml-auto text-[10px] text-state-emerging font-medium">{message}</span>
                    )}
                  </div>
                  <span className="text-[11px] text-ink-muted font-normal">{action.desc}</span>
                </Button>
              )
            })}
          </div>
        </CardContent>
      </Card>
    </>
  )
}
