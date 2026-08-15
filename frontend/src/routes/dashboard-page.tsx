import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Activity, Database, GitBranch, Network, TrendingUp, Users } from 'lucide-react'
import { CompassMark } from '@/components/layout/compass-mark'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { apiGet } from '@/lib/api'
import type { components } from '@/types/api'

interface StatItem {
  label: string
  value: string
  delta: string
  icon: typeof Network
  hint: string
  deltaType: 'up' | 'neutral'
}

interface ActivityItem {
  id: string
  time: string
  icon: typeof Network
  title: string
  desc: string
  color: string
}

/** 后端 /admin/crawl/status 返回项（契约 CrawlPlatform） */
type CrawlPlatform = components['schemas']['CrawlPlatform']


/** 真实数据驱动的统计卡 */
const EMPTY_STATS: StatItem[] = [
  { label: '图谱节点', value: '—', delta: '加载中', icon: Network, hint: '—', deltaType: 'neutral' },
  { label: '累计采集量', value: '—', delta: '—', icon: Database, hint: '—', deltaType: 'neutral' },
  { label: '已解析简历', value: '—', delta: '—', icon: Users, hint: '—', deltaType: 'neutral' },
  { label: '图谱版本', value: '—', delta: '—', icon: GitBranch, hint: '—', deltaType: 'neutral' },
]

const QUICK_LINKS = [
  { to: '/graph', icon: Network, title: '能力图谱', desc: '2D 力导向图为主，3D 模式可选。四种视图切换：全景 / 技术栈 / 级别 / 岗位中心', badge: '真实' },
  { to: '/evolution', icon: TrendingUp, title: '演化看板', desc: '图谱版本快照追踪技能频次变化，Z-score 检测新兴/衰退技能', badge: '真实' },
  { to: '/resume-match', icon: Users, title: '简历匹配', desc: '上传简历 → LLM 解析 → 三维加权匹配 → 差距分析', badge: '真实' },
  { to: '/admin/crawl', icon: Database, title: '爬取管理', desc: '13 源采集状态 · 真实 output/raw 统计', badge: 'admin' },
]

/**
 * 仪表盘 — 系统总览
 *
 * 数据来源：真实后端 API
 * - /graph/panorama → 图谱节点统计
 * - /admin/crawl/status → 采集量 + 数据源
 * - /resume/list → 已解析简历数
 * - /evolution/versions + /admin/audit/logs → 最近活动流
 * 后端未产出的指标（稳定岗位/匹配任务等）不展示 mock，由真实可派生指标替代。
 */
export function DashboardPage() {
  const [stats, setStats] = useState<StatItem[]>(EMPTY_STATS)
  const [sources, setSources] = useState<CrawlPlatform[]>([])
  const [activities, setActivities] = useState<ActivityItem[]>([])
  const [graphNodes, setGraphNodes] = useState(0)
  const [sourceCount, setSourceCount] = useState(0)
  const [crawlAvailable, setCrawlAvailable] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.allSettled([
      apiGet<components['schemas']['GraphViewData']>('/graph/panorama?limit=200&min_weight=0.3'),
      // 采集统计需 admin 权限：游客 401 时静默降级，不触发全局登出
      apiGet<components['schemas']['CrawlStatusData']>('/admin/crawl/status', { skipAuthRedirect: true }),
      // 简历/采集/审计统计均需认证：游客 401 时静默降级，不触发全局登出
      apiGet<{ items: unknown[]; total: number }>('/resume/list?limit=100', { skipAuthRedirect: true }),
      apiGet<components['schemas']['EvolutionVersionListData']>('/evolution/versions?page=1&size=10', { skipAuthRedirect: true }),
      apiGet<components['schemas']['AuditLogsData']>('/admin/audit/logs?page=1&size=10', { skipAuthRedirect: true }),
    ]).then(([graphRes, crawlRes, resumeRes, versionRes, auditRes]) => {
      if (cancelled) return

      const graph = graphRes.status === 'fulfilled' ? graphRes.value.stats : null
      const platforms = crawlRes.status === 'fulfilled' ? crawlRes.value.platforms : []
      const resumeTotal = resumeRes.status === 'fulfilled' ? resumeRes.value.total : 0
      const versions = versionRes.status === 'fulfilled' ? versionRes.value.items : []
      const logs = auditRes.status === 'fulfilled' ? auditRes.value.items : []

      setGraphNodes(graph?.nodes ?? 0)
      setCrawlAvailable(crawlRes.status === 'fulfilled')
      setSourceCount(platforms.length)
      setSources(platforms)

      const collectTotal = platforms.reduce((s, p) => s + p.total_count, 0)
      // 采集统计需 admin 权限：非 admin（403 降级）时显示 '—' 而非 "0"——
      // 0 会被误读为"系统无采集数据"（08-15 修复，与图谱节点卡口径一致）
      const collectOk = crawlRes.status === 'fulfilled'
      setStats([
        { label: '图谱节点', value: graph ? String(graph.nodes) : '—', delta: `${graph?.edges ?? 0} 边`, icon: Network, hint: 'Neo4j 岗位-技能关系', deltaType: graph ? 'up' : 'neutral' },
        { label: '累计采集量', value: collectOk ? collectTotal.toLocaleString() : '—', delta: collectOk ? `${platforms.length} 源` : '—', icon: Database, hint: 'output/*.jsonl 真实行数', deltaType: platforms.length ? 'up' : 'neutral' },
        { label: '已解析简历', value: String(resumeTotal), delta: 'resume_cache', icon: Users, hint: '可发起真实匹配', deltaType: resumeTotal ? 'up' : 'neutral' },
        { label: '图谱版本', value: String(versions.length), delta: versions[0]?.version_id ?? '—', icon: GitBranch, hint: 'T+1 快照 · 可 diff', deltaType: versions.length ? 'up' : 'neutral' },
      ])

      // 最近活动 = 版本发布（优先全部展示）+ 登录审计补足至 6 条
      const versionActs: ActivityItem[] = versions.map((v) => ({
        id: `v-${v.version_id}`,
        time: v.created_at ? new Date(v.created_at).toLocaleString('zh-CN') : '—',
        icon: Network,
        title: `图谱版本 ${v.version_id} 发布`,
        desc: v.change_summary || '版本快照',
        color: 'bg-state-stable',
      }))
      const auditActs: ActivityItem[] = logs.map((l) => ({
        id: `a-${l.id}`,
        time: l.created_at ? new Date(l.created_at).toLocaleString('zh-CN') : '—',
        icon: Activity,
        title: `${l.detail?.username ?? '用户'} 登录`,
        desc: l.action,
        color: 'bg-state-candidate',
      }))
      const remaining = Math.max(0, 6 - versionActs.length)
      setActivities(
        [...versionActs, ...auditActs.slice(0, remaining)]
          .sort((a, b) => (a.time > b.time ? -1 : 1))
          .slice(0, 6),
      )
    })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <>
      <PageHeader
        title="仪表盘"
        description="多源异构驱动的岗位能力动态演化与人岗匹配系统"
      />

      {/* 签名区域 — 罗盘标记 + 系统状态（真实指标） */}
      <Card className="mb-6 overflow-hidden">
        <CardContent className="flex items-center gap-6 py-8">
          <CompassMark size="lg" active className="shrink-0" />
          <div className="space-y-1 flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold">智岗罗盘</h2>
              <Badge variant="outline" className="font-mono">v0.1.0</Badge>
              <Badge variant="outline" className="text-xs font-mono text-state-emerging border-state-emerging/30">
                真实 API 已接入
              </Badge>
            </div>
            <p className="text-sm text-ink-muted">
              证据驱动的人才能力大脑 — 每条技能断言可追溯至原始 JD / 论文 / 社区信号
            </p>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-2 text-xs text-ink-faint">
              <span className="flex items-center gap-1">
                <span className="size-1.5 rounded-full bg-state-emerging" />
                {crawlAvailable ? `采集管线 · ${sourceCount}/13 源有采集记录` : '采集统计 · 登录后查看'}
              </span>
              <span className="flex items-center gap-1">
                <span className="size-1.5 rounded-full bg-state-stable" />
                图谱服务 · {graphNodes} 节点
              </span>
              <span className="flex items-center gap-1">
                <span className="size-1.5 rounded-full bg-state-candidate" />
                匹配引擎 · 真实 recommend/compare
              </span>
              <span className="flex items-center gap-1">
                <span className="size-1.5 rounded-full bg-ink-faint" />
                演化信号 · M4 待交付
              </span>
            </div>
          </div>
          <div className="hidden md:flex flex-col items-end gap-1 text-right">
            <p className="text-xs text-ink-muted">数据来源</p>
            <p className="text-sm font-mono text-ink">Postgres + Neo4j + Redis</p>
            <p className="text-[10px] text-ink-faint">docker compose 4 服务</p>
          </div>
        </CardContent>
      </Card>

      {/* 关键指标卡片（真实数据） */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {stats.map((stat) => {
          const Icon = stat.icon
          const deltaColor = stat.deltaType === 'up' ? 'text-state-emerging' : 'text-ink-muted'
          return (
            <Card key={stat.label}>
              <CardContent className="py-4">
                <div className="flex items-center justify-between mb-2">
                  <Icon className="size-4 text-ink-faint" />
                  <span className={`text-xs font-mono ${deltaColor}`}>{stat.delta}</span>
                </div>
                <div className="text-2xl font-semibold tracking-tight tabular-nums">{stat.value}</div>
                <div className="text-xs text-ink-muted mt-1">{stat.label}</div>
                <div className="text-[10px] text-ink-faint mt-0.5 truncate">{stat.hint}</div>
              </CardContent>
            </Card>
          )
        })}
      </div>

      {/* 最近活动（真实版本发布 + 登录审计） + 快捷入口 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-sm flex items-center justify-between">
              <span>最近活动</span>
              <span className="text-xs font-normal text-ink-faint">图谱版本 + 审计日志</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {activities.length === 0 ? (
              <p className="py-10 text-center text-sm text-ink-faint">
                暂无活动记录 · 版本发布与登录行为将在此展示
              </p>
            ) : (
              activities.map((act) => {
                const Icon = act.icon
                return (
                  <div key={act.id} className="flex items-start gap-3">
                    <div className="flex flex-col items-center gap-1 pt-0.5">
                      <span className={`size-2 rounded-full ${act.color}`} />
                      <div className="w-px flex-1 bg-border min-h-[24px]" />
                    </div>
                    <div className="flex-1 min-w-0 pb-3">
                      <div className="flex items-center gap-2">
                        <Icon className="size-3.5 text-ink-muted shrink-0" />
                        <p className="text-sm text-ink truncate">{act.title}</p>
                        <span className="text-[10px] font-mono text-ink-faint ml-auto shrink-0">{act.time}</span>
                      </div>
                      <p className="text-xs text-ink-muted mt-0.5">{act.desc}</p>
                    </div>
                  </div>
                )
              })
            )}
          </CardContent>
        </Card>

        {/* 快捷入口 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">快捷入口</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {QUICK_LINKS.map((link) => {
              const Icon = link.icon
              return (
                <Link
                  key={link.to}
                  to={link.to}
                  className="block rounded-md border border-border p-3 transition-colors hover:bg-subtle hover:border-border-strong"
                >
                  <div className="flex items-center gap-2 mb-1">
                    <Icon className="size-3.5 text-ink-secondary" />
                    <span className="text-sm font-medium text-ink">{link.title}</span>
                    <Badge variant="outline" className="text-[10px] ml-auto font-mono">{link.badge}</Badge>
                  </div>
                  <p className="text-xs text-ink-muted leading-relaxed">{link.desc}</p>
                </Link>
              )
            })}
          </CardContent>
        </Card>
      </div>

      {/* 数据源底座（真实采集统计） */}
      <Card className="mt-4">
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Database className="size-4" />
            <span>数据源采集统计</span>
            <Badge variant="outline" className="text-xs ml-auto font-mono">
              有采集记录 {sourceCount}/13 源
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-2 text-xs">
            {sources.map((src) => (
              <div key={src.id} className="rounded-md border p-2">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[10px] text-ink-faint">{src.level}</span>
                </div>
                <p className="text-ink mt-0.5 truncate">{src.name}</p>
                <p className="text-ink-muted font-mono tabular-nums">{src.total_count.toLocaleString()}</p>
              </div>
            ))}
            {sources.length === 0 && (
              <p className="col-span-7 py-6 text-center text-ink-faint">
                {crawlAvailable ? '暂无采集记录，请先运行爬虫' : '采集统计需 admin 登录后查看'}
              </p>
            )}
          </div>
        </CardContent>
      </Card>
    </>
  )
}
