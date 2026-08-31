import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { Activity, Database, GitBranch, Network, TrendingUp, Users } from 'lucide-react'
// 按需导入（第八轮 P2-32：与本仓 charts.tsx/graph-2d.tsx 口径一致，
// 本页仅用 Bar + Grid(x/y 轴)/Legend/Tooltip 的 Canvas 渲染）
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { MetricCard } from '@/components/shared/metric-card'
import { Reveal } from '@/components/ui/reveal'
import { apiGet } from '@/lib/api'
import { escapeHtml, formatDateTime, isDark } from '@/lib/utils'
import type { components } from '@/types/api'

echarts.use([BarChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

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
  /** 原始时间戳（排序用——formatDateTime 后的字符串无法正确比较日期） */
  ts: number
  time: string
  icon: typeof Network
  title: string
  desc: string
  color: string
}

/** 后端 /admin/crawl/status 返回项（契约 CrawlPlatform） */
type CrawlPlatform = components['schemas']['CrawlPlatform']

/** 后端 /evolution/versions 返回项（契约 EvolutionVersion） */
type EvolutionVersion = components['schemas']['EvolutionVersion']

/** 图谱版本演化趋势条形图 — 版本快照的新增/变更/移除节点数（仪表盘首屏图表）
 *
 * 数据与「图谱版本」指标卡同源（/evolution/versions 前 10 条，无需新增端点）；
 * 移除用警示橙、新增用信号绿，与岗位状态机语义一致。 */
function VersionTrendChart({ versions }: { versions: EvolutionVersion[] }) {
  const elRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const [themeVersion, setThemeVersion] = useState(0)

  useEffect(() => {
    const el = document.documentElement
    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.attributeName === 'class') {
          setThemeVersion((v) => v + 1)
          return
        }
      }
    })
    observer.observe(el, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const el = elRef.current
    if (!el || versions.length === 0) return
    const dark = isDark()
    const ink = dark ? '#fafafa' : '#09090b'
    const muted = dark ? '#a1a1aa' : '#71717a'
    const border = dark ? '#27272a' : '#e4e4e7'
    // 接口按新→旧返回，图表按时间正序绘制（左旧右新）
    const asc = [...versions].reverse()
    const chart = echarts.init(el)
    chartRef.current = chart
    chart.setOption({
      animation: false,
      grid: { left: 8, right: 8, top: 30, bottom: 2, containLabel: true },
      legend: {
        top: 0,
        right: 0,
        itemWidth: 10,
        itemHeight: 6,
        itemGap: 12,
        textStyle: { fontSize: 10, color: muted },
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: dark ? '#18181b' : '#ffffff',
        borderColor: border,
        textStyle: { color: ink, fontSize: 12 },
        formatter: (params: unknown) => {
          const list = (Array.isArray(params) ? params : [params]) as { name?: string; seriesName?: string; value?: number; marker?: string }[]
          if (list.length === 0) return ''
          const v = asc.find((x) => x.version_id === list[0].name)
          const lines = [
            `<b>${escapeHtml(list[0].name ?? '')}</b>`,
            v?.created_at ? `<span style="color:${muted};font-size:11px">${formatDateTime(v.created_at)}</span>` : '',
            ...list.map((p) => `${p.marker ?? ''}${p.seriesName}: <b>${p.value ?? 0}</b>`),
          ]
          return lines.filter(Boolean).join('<br/>')
        },
      },
      xAxis: {
        type: 'category',
        data: asc.map((v) => v.version_id),
        axisLabel: { fontSize: 10, color: muted, hideOverlap: true },
        axisLine: { lineStyle: { color: border } },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        axisLabel: { fontSize: 10, color: muted },
        splitLine: { lineStyle: { color: border, type: 'dashed' } },
      },
      series: [
        { name: '新增', type: 'bar', data: asc.map((v) => v.node_added), itemStyle: { color: '#10b981', borderRadius: [2, 2, 0, 0] }, barGap: '25%' },
        { name: '变更', type: 'bar', data: asc.map((v) => v.node_changed), itemStyle: { color: '#3b82f6', borderRadius: [2, 2, 0, 0] } },
        { name: '移除', type: 'bar', data: asc.map((v) => v.node_removed), itemStyle: { color: '#f59e0b', borderRadius: [2, 2, 0, 0] } },
      ],
    })
    const observer = new ResizeObserver(() => chartRef.current?.resize())
    observer.observe(el)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [versions, themeVersion])

  if (versions.length === 0) {
    return (
      <p className="flex h-52 items-center justify-center text-center text-xs text-ink-faint">
        暂无版本快照 —— 图谱每日 T+1 快照发布后，此处展示节点新增/变更/移除趋势
      </p>
    )
  }
  return <div ref={elRef} className="h-52 w-full" aria-label="图谱版本演化趋势图" role="img" />
}


/** 真实数据驱动的统计卡 */
const EMPTY_STATS: StatItem[] = [
  { label: '图谱节点', value: '—', delta: '加载中', icon: Network, hint: '—', deltaType: 'neutral' },
  { label: '累计采集量', value: '—', delta: '—', icon: Database, hint: '—', deltaType: 'neutral' },
  { label: '已解析简历', value: '—', delta: '—', icon: Users, hint: '—', deltaType: 'neutral' },
  { label: '图谱版本', value: '—', delta: '—', icon: GitBranch, hint: '—', deltaType: 'neutral' },
]

const QUICK_LINKS = [
  { to: '/graph', icon: Network, title: '能力图谱', desc: '2D 力导向图为主，3D 模式可选。视图切换：全景 / 技术栈 / 岗位画像', badge: '真实' },
  { to: '/evolution', icon: TrendingUp, title: '演化看板', desc: '图谱版本快照追踪技能频次变化，Z-score 检测新兴/衰退技能', badge: '真实' },
  { to: '/resume-match', icon: Users, title: '简历匹配', desc: '上传简历 → LLM 解析 → 三维加权匹配 → 差距分析', badge: '真实' },
  { to: '/admin/crawl', icon: Database, title: '爬取管理', desc: '13 源采集状态 · 真实统计（DB 入库口径）', badge: 'admin' },
]

/**
 * 仪表盘 — 系统总览
 *
 * 数据来源：真实后端 API
 * - /graph/view/panorama → 图谱节点统计（与图谱页共用统一视图端点）
 * - /admin/crawl/status → 采集量 + 数据源
 * - /resume/list → 已解析简历数
 * - /evolution/versions + /admin/audit/logs → 最近活动流
 * 后端未产出的指标（稳定岗位/匹配任务等）不展示 mock，由真实可派生指标替代。
 */
export function DashboardPage() {
  const [stats, setStats] = useState<StatItem[]>(EMPTY_STATS)
  const [sources, setSources] = useState<CrawlPlatform[]>([])
  const [activities, setActivities] = useState<ActivityItem[]>([])
  const [versions, setVersions] = useState<EvolutionVersion[]>([])
  const [sourceCount, setSourceCount] = useState(0)
  const [crawlAvailable, setCrawlAvailable] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.allSettled([
      // 系统级概览接口加 60s TTL 缓存：返回时秒开，避免每次进入都重拉；
      // resume/list 为用户私有数据，保留实时，不缓存（游客场景也看得到本人简历）
      apiGet<components['schemas']['GraphViewData']>('/graph/view/panorama?limit=200', { ttl: 60 }),
      apiGet<components['schemas']['CrawlStatusData']>('/admin/crawl/status', { skipAuthRedirect: true, ttl: 60 }),
      apiGet<{ items: unknown[]; total: number }>('/resume/list?limit=100', { skipAuthRedirect: true }),
      apiGet<components['schemas']['EvolutionVersionListData']>('/evolution/versions?page=1&size=10', { skipAuthRedirect: true, ttl: 60 }),
      apiGet<components['schemas']['AuditLogsData']>('/admin/audit/logs?page=1&size=10', { skipAuthRedirect: true, ttl: 60 }),
    ]).then(([graphRes, crawlRes, resumeRes, versionRes, auditRes]) => {
      if (cancelled) return

      const graph = graphRes.status === 'fulfilled' ? graphRes.value.stats : null
      const platforms = crawlRes.status === 'fulfilled' ? crawlRes.value.platforms : []
      const resumeTotal = resumeRes.status === 'fulfilled' ? resumeRes.value.total : 0
      const versions = versionRes.status === 'fulfilled' ? versionRes.value.items : []
      const logs = auditRes.status === 'fulfilled' ? auditRes.value.items : []

      setCrawlAvailable(crawlRes.status === 'fulfilled')
      setSourceCount(platforms.length)
      setSources(platforms)
      setVersions(versions)

      const collectTotal = platforms.reduce((s, p) => s + p.total_count, 0)
      // 采集统计需 admin 权限：非 admin（403 降级）时显示 '—' 而非 "0"——
      // 0 会被误读为"系统无采集数据"（08-15 修复，与图谱节点卡口径一致）
      const collectOk = crawlRes.status === 'fulfilled'
      setStats([
        { label: '图谱节点', value: graph ? String(graph.nodes) : '—', delta: `${graph?.edges ?? 0} 边`, icon: Network, hint: 'Neo4j 岗位-技能关系', deltaType: graph ? 'up' : 'neutral' },
        { label: '累计采集量', value: collectOk ? collectTotal.toLocaleString() : '—', delta: collectOk ? `${platforms.length} 源` : '—', icon: Database, hint: 'DB 入库总量（JD/课程/论文/社区）', deltaType: platforms.length ? 'up' : 'neutral' },
        { label: '已解析简历', value: String(resumeTotal), delta: 'resume_cache', icon: Users, hint: '可发起真实匹配', deltaType: resumeTotal ? 'up' : 'neutral' },
        { label: '图谱版本', value: String(versions.length), delta: versions[0]?.version_id ?? '—', icon: GitBranch, hint: 'T+1 快照 · 可 diff', deltaType: versions.length ? 'up' : 'neutral' },
      ])

      // 最近活动 = 版本发布（优先全部展示）+ 登录审计补足至 6 条
      const versionActs: ActivityItem[] = versions.map((v) => ({
        id: `v-${v.version_id}`,
        ts: Date.parse(v.created_at ?? '') || 0,
        time: formatDateTime(v.created_at),
        icon: Network,
        title: `图谱版本 ${v.version_id} 发布`,
        desc: v.change_summary || '版本快照',
        color: 'bg-state-stable',
      }))
      const auditActs: ActivityItem[] = logs.map((l) => ({
        id: `a-${l.id}`,
        ts: Date.parse(l.created_at ?? '') || 0,
        time: formatDateTime(l.created_at),
        icon: Activity,
        title: `${l.detail?.username ?? '用户'} 登录`,
        desc: l.action,
        color: 'bg-state-candidate',
      }))
      const remaining = Math.max(0, 6 - versionActs.length)
      setActivities(
        [...versionActs, ...auditActs.slice(0, remaining)]
          .sort((a, b) => b.ts - a.ts)
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

      {/* 关键指标卡片（真实数据）——复用 shared MetricCard（统一指标卡形态）；分级入场：卡片逐个错峰浮现 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {stats.map((stat, i) => (
          <Reveal key={stat.label} delay={i * 90} className="h-full">
            <MetricCard
              className="h-full"
              data={{
                label: stat.label,
                value: stat.value,
                delta: stat.delta,
                deltaTone: stat.deltaType === 'up' ? 'emerging' : 'muted',
                hint: stat.hint,
                icon: stat.icon,
              }}
            />
          </Reveal>
        ))}
      </div>

      {/* 最近活动（真实版本发布 + 登录审计） + 快捷入口 */}
      <Reveal delay={360}>
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
                        <span className="text-[11px] font-mono text-ink-faint ml-auto shrink-0">{act.time}</span>
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
                    <Badge variant="outline" className="text-[11px] ml-auto font-mono">{link.badge}</Badge>
                  </div>
                  <p className="text-xs text-ink-muted leading-relaxed">{link.desc}</p>
                </Link>
              )
            })}
          </CardContent>
        </Card>
      </div>
      </Reveal>

      {/* 图谱版本演化趋势（与「图谱版本」指标卡同源，无需额外端点） */}
      <Reveal delay={460} className="mt-4">
        <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <TrendingUp className="size-4" />
            <span>图谱版本演化趋势</span>
            <span className="text-xs font-normal text-ink-faint ml-auto">
              近 {versions.length} 版 · 节点新增 / 变更 / 移除
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <VersionTrendChart versions={versions} />
        </CardContent>
      </Card>
      </Reveal>

      {/* 数据源底座（真实采集统计） */}
      <Reveal delay={560} className="mt-4">
        <Card>
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
          {/* 各源采集量按全源最大值等比出条——数字卡升维为可比对的视觉量纲 */}
          {(() => {
            const maxSource = sources.reduce((m, s) => Math.max(m, s.total_count), 0)
            return (
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-2 text-xs">
                {sources.map((src) => (
                  <div key={src.id} className="rounded-md border p-2">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-[11px] text-ink-faint">{src.level}</span>
                    </div>
                    <p className="text-ink mt-0.5 truncate">{src.name}</p>
                    <p className="text-ink-muted font-mono tabular-nums">{src.total_count.toLocaleString()}</p>
                    {maxSource > 0 && (
                      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-subtle" aria-hidden="true">
                        <div
                          className="h-full rounded-full bg-ink/45"
                          style={{ width: `${Math.max(2, Math.round((src.total_count / maxSource) * 100))}%` }}
                        />
                      </div>
                    )}
                  </div>
                ))}
                {sources.length === 0 && (
                  <p className="col-span-7 py-6 text-center text-ink-faint">
                    {crawlAvailable ? '暂无采集记录，请先运行爬虫' : '采集统计需 admin 登录后查看'}
                  </p>
                )}
              </div>
            )
          })()}
        </CardContent>
      </Card>
      </Reveal>
    </>
  )
}
