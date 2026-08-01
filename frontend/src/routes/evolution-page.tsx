/**
 * 演化看板页 — 设计文档 §7 动态演化与新岗位发现
 *
 * 当前阶段（M3 前端提前启动）：
 * - 数据来源：本地 mock（MOCK_*），后端 /api/v1/evolution/* 就绪后改用 apiGet
 * - 已实现：90 天频次趋势 + Top-10 新兴/衰退 + 版本 diff + 岗位六状态机流转
 * - 暗色模式跟随 documentElement.classList 的 .dark 类（MutationObserver 监听）
 *
 * 设计依据：
 * - Z-score 阈值 emerging > 2.0 / declining < -1.5（§7.1）
 * - 频次 < 10 的技能受小基数保护不参与判定（mock 中衰退项频次均 ≥ 10）
 * - T+1 更新承诺：每日 05:00 发布 graph_v{date}.json 全量快照，保留 90 天
 * - 岗位六状态机：candidate/emerging/stable/declining/archived/rejected（§7.2.1）
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { TooltipComponent, GridComponent, LegendComponent, MarkLineComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { ArrowDownRight, ArrowUpRight, Calendar, GitBranch, Minus } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'

// 按需注册 ECharts 模块 — 相比 `import * as echarts from 'echarts'`，减少约 70% bundle 体积
echarts.use([LineChart, TooltipComponent, GridComponent, LegendComponent, MarkLineComponent, CanvasRenderer])

// ===== Types =====

type TrendTone = 'emerging' | 'declining' | 'stable'
type PositionStatus = 'candidate' | 'emerging' | 'stable' | 'declining' | 'archived' | 'rejected'

interface TrendSkill {
  name: string
  // 90 天 JD 频次时序，与 MOCK_TREND_DATES 一一对应
  freq: number[]
  // 当前 30 天窗口 z-score
  zScore: number
  // 折线着色 — rising(z>1.5) 也归入 emerging 色系表达"向上"信号
  tone: TrendTone
}

interface SignalSkill {
  name: string
  zScore: number
  // 环比 MoM 变化百分比
  mom: number
  // 当前 30 天窗口总频次
  freq: number
  // 30 天 mini 频次时序，用于 sparkline
  sparkline: number[]
}

interface VersionDiffItem {
  name: string
  type: 'position' | 'skill' | 'evidence'
  change: 'added' | 'removed' | 'changed'
  detail: string
}

interface StateTransition {
  date: string
  positionName: string
  from: PositionStatus
  to: PositionStatus
  count: number
  trigger: 'auto' | 'manual'
}

interface MetricItem {
  key: string
  label: string
  value: string | number
  delta: number
  tone: TrendTone
  hint: string
}

// ===== Mock data =====
// 后端就绪后替换为：
// - GET /api/v1/evolution/summary         → MOCK_METRICS
// - GET /api/v1/evolution/trend           → MOCK_TREND_SKILLS
// - GET /api/v1/evolution/signals         → MOCK_EMERGING / MOCK_DECLINING
// - GET /api/v1/evolution/versions/diff   → MOCK_VERSION_DIFF
// - GET /api/v1/evolution/state-machine   → MOCK_STATE_DISTRIBUTION / MOCK_TRANSITIONS

const MOCK_METRICS: MetricItem[] = [
  { key: 'total', label: '监控技能总数', value: 1082, delta: 38, tone: 'stable', hint: '90 天滑动窗口 · 频次 ≥ 10 入监测' },
  { key: 'emerging', label: '新兴信号数', value: 14, delta: 3, tone: 'emerging', hint: 'z > 2.0 阈值命中' },
  { key: 'declining', label: '衰退信号数', value: 5, delta: -1, tone: 'declining', hint: 'z < -1.5 阈值命中' },
  { key: 'version', label: '当前版本号', value: 'v20260729', delta: 1, tone: 'stable', hint: 'T+1 05:00 发布 · 保留 90 天' },
]

// 生成最近 90 天日期标签（MM-DD），固定基线 2026-07-29 — 与后端 T+1 节奏对齐
const MOCK_TREND_DATES: string[] = (() => {
  const end = new Date('2026-07-29T00:00:00Z')
  const arr: string[] = []
  for (let i = 89; i >= 0; i--) {
    const d = new Date(end.getTime() - i * 24 * 3600 * 1000)
    arr.push(`${(d.getUTCMonth() + 1).toString().padStart(2, '0')}-${d.getUTCDate().toString().padStart(2, '0')}`)
  }
  return arr
})()

// LCG 伪随机 — 保证 mock 时序在多次渲染间稳定，避免开发态 HMR 时数据抖动
function makeRand(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (s * 1103515245 + 12345) & 0x7fffffff
    return s / 0x7fffffff
  }
}

// 生成 90 天频次时序 — base + 线性趋势 + 周内波动 + 伪随机噪声
function genFreq(base: number, trendPerDay: number, noiseAmp: number, seed: number): number[] {
  const rand = makeRand(seed)
  return Array.from({ length: 90 }, (_, i) => {
    const weekly = Math.sin((i * 2 * Math.PI) / 7) * noiseAmp * 0.25
    const noise = (rand() - 0.5) * noiseAmp
    return Math.max(0, Math.round(base + trendPerDay * i + weekly + noise))
  })
}

// 生成 30 天 sparkline 数据 — 方向与 tone 一致
function genSparkline(direction: 'up' | 'down', base: number, amp: number, seed: number): number[] {
  const rand = makeRand(seed)
  const trend = direction === 'up' ? 0.04 : -0.04
  return Array.from({ length: 30 }, (_, i) => {
    const noise = (rand() - 0.5) * amp
    return Math.max(0, Math.round(base + trend * i * base * 0.1 + noise))
  })
}

const MOCK_TREND_SKILLS: TrendSkill[] = [
  // React：高位缓慢下行（z=-0.82，仍处稳定区间）
  { name: 'React', freq: genFreq(180, -0.3, 18, 11), zScore: -0.82, tone: 'stable' },
  // Vue：中位缓降（z=-1.18，接近 declining 阈值但未触发）
  { name: 'Vue', freq: genFreq(95, -0.2, 14, 23), zScore: -1.18, tone: 'stable' },
  // LangChain：低位快速上升（z=2.34，命中 emerging）
  { name: 'LangChain', freq: genFreq(12, 0.85, 6, 37), zScore: 2.34, tone: 'emerging' },
]

// 新兴 Top-10 — 按 z-score 降序，含 emerging(z>2.0) 与 rising(z>1.5) 两档
const MOCK_EMERGING: SignalSkill[] = [
  { name: 'LangChain', zScore: 2.34, mom: 180, freq: 142, sparkline: genSparkline('up', 30, 8, 101) },
  { name: 'LlamaIndex', zScore: 2.18, mom: 156, freq: 98, sparkline: genSparkline('up', 22, 6, 102) },
  { name: 'Spring AI', zScore: 2.05, mom: 142, freq: 86, sparkline: genSparkline('up', 18, 5, 103) },
  { name: 'v0 CLI', zScore: 1.95, mom: 98, freq: 64, sparkline: genSparkline('up', 14, 4, 104) },
  { name: 'Cursor API', zScore: 1.88, mom: 87, freq: 58, sparkline: genSparkline('up', 12, 4, 105) },
  { name: 'Bun', zScore: 1.78, mom: 68, freq: 72, sparkline: genSparkline('up', 16, 5, 106) },
  { name: 'Astro', zScore: 1.72, mom: 62, freq: 54, sparkline: genSparkline('up', 12, 4, 107) },
  { name: 'Drizzle ORM', zScore: 1.65, mom: 54, freq: 38, sparkline: genSparkline('up', 9, 3, 108) },
  { name: 'Turso', zScore: 1.58, mom: 48, freq: 32, sparkline: genSparkline('up', 8, 3, 109) },
  { name: 'Claude API', zScore: 1.52, mom: 44, freq: 41, sparkline: genSparkline('up', 10, 3, 110) },
]

// 衰退 Top-10 — 按 z-score 升序，频次均 ≥ 10（避开小基数保护）
const MOCK_DECLINING: SignalSkill[] = [
  { name: 'JSP/Servlet', zScore: -2.45, mom: -68, freq: 18, sparkline: genSparkline('down', 40, 6, 201) },
  { name: 'jQuery', zScore: -2.28, mom: -52, freq: 32, sparkline: genSparkline('down', 60, 8, 202) },
  { name: 'Apache Velocity', zScore: -2.12, mom: -45, freq: 11, sparkline: genSparkline('down', 22, 4, 203) },
  { name: 'COBOL', zScore: -1.98, mom: -38, freq: 14, sparkline: genSparkline('down', 26, 4, 204) },
  { name: 'Struts2', zScore: -1.85, mom: -42, freq: 12, sparkline: genSparkline('down', 24, 4, 205) },
  { name: 'Apache Ant', zScore: -1.78, mom: -34, freq: 19, sparkline: genSparkline('down', 32, 5, 206) },
  { name: 'JBuilder', zScore: -1.72, mom: -28, freq: 10, sparkline: genSparkline('down', 18, 3, 207) },
  { name: 'EJB', zScore: -1.68, mom: -32, freq: 16, sparkline: genSparkline('down', 28, 4, 208) },
  { name: 'Backbone.js', zScore: -1.62, mom: -26, freq: 13, sparkline: genSparkline('down', 22, 4, 209) },
  { name: 'PhoneGap', zScore: -1.55, mom: -22, freq: 11, sparkline: genSparkline('down', 18, 3, 210) },
]

// 可选版本列表 — 最近 6 个 T+1 发布版本
const MOCK_VERSIONS = [
  'v20260729', 'v20260728', 'v20260725', 'v20260724', 'v20260722', 'v20260721',
] as const

// v20260729 vs v20260728 的 diff — mock 仅此一对真实数据，其他组合显示占位
const MOCK_VERSION_DIFF: VersionDiffItem[] = [
  { name: 'MCP Server 开发工程师', type: 'position', change: 'added', detail: 'JD 38 条 · 跨 3 源 · z=2.34' },
  { name: 'LangGraph', type: 'skill', change: 'added', detail: 'JD 24 条 · 新增入图谱' },
  { name: 'v0 CLI', type: 'skill', change: 'added', detail: 'JD 18 条 · 跨 2 源' },
  { name: 'Claude Sonnet 4.5', type: 'skill', change: 'added', detail: 'JD 22 条 · 替换旧 Anthropic API 表述' },
  { name: 'Drizzle ORM', type: 'skill', change: 'added', detail: 'JD 16 条 · TypeScript ORM 上升信号' },
  { name: 'JSP/Servlet', type: 'skill', change: 'removed', detail: '频次 < 10 · 触发小基数保护下架' },
  { name: 'Apache Velocity', type: 'skill', change: 'removed', detail: '频次 11 · 接近下架阈值' },
  { name: 'PhoneGap 移动开发', type: 'position', change: 'removed', detail: 'archived · JD 频次连续 3 窗口 < 5' },
  { name: '前端开发工程师', type: 'position', change: 'changed', detail: 'stable 保持 · skill_novelty 0.18 → 0.22' },
  { name: 'Java 开发工程师', type: 'position', change: 'changed', detail: '新增 Spring AI 依赖边 · weight 0.62' },
  { name: 'React', type: 'skill', change: 'changed', detail: 'PageRank 0.87 → 0.85 · 略降' },
  { name: 'Docker', type: 'skill', change: 'changed', detail: '权重 necessity → must · 跨 4 源一致' },
  { name: 'LangChain', type: 'skill', change: 'changed', detail: 'PageRank 0.42 → 0.68 · 跃升' },
]

const MOCK_STATE_DISTRIBUTION: ReadonlyArray<{ status: PositionStatus; count: number }> = [
  { status: 'candidate', count: 12 },
  { status: 'emerging', count: 14 },
  { status: 'stable', count: 86 },
  { status: 'declining', count: 5 },
  { status: 'archived', count: 23 },
  { status: 'rejected', count: 2 },
]

const MOCK_TRANSITIONS: StateTransition[] = [
  { date: '2026-07-29', positionName: 'MCP Server 开发工程师', from: 'candidate', to: 'emerging', count: 1, trigger: 'manual' },
  { date: '2026-07-29', positionName: 'v0 CLI 工程师', from: 'candidate', to: 'rejected', count: 1, trigger: 'manual' },
  { date: '2026-07-28', positionName: 'LangChain 工程师', from: 'emerging', to: 'stable', count: 1, trigger: 'auto' },
  { date: '2026-07-28', positionName: 'jQuery 前端', from: 'stable', to: 'declining', count: 1, trigger: 'auto' },
  { date: '2026-07-27', positionName: 'PhoneGap 移动开发', from: 'declining', to: 'archived', count: 1, trigger: 'manual' },
  { date: '2026-07-26', positionName: 'Spring AI 工程师', from: 'candidate', to: 'emerging', count: 1, trigger: 'manual' },
  { date: '2026-07-25', positionName: 'COBOL 维护', from: 'declining', to: 'stable', count: 1, trigger: 'auto' },
  { date: '2026-07-24', positionName: 'DevOps 工程师', from: 'emerging', to: 'stable', count: 2, trigger: 'auto' },
]

// 状态机元数据 — 颜色/中文标签/Badge variant（与 globals.css 状态色对齐）
const STATUS_META: Record<PositionStatus, { label: string; color: string; badgeVariant: 'candidate' | 'emerging' | 'stable' | 'declining' | 'archived' }> = {
  candidate: { label: '候选', color: '#71717a', badgeVariant: 'candidate' },
  emerging: { label: '新兴', color: '#10b981', badgeVariant: 'emerging' },
  stable: { label: '稳定', color: '#3b82f6', badgeVariant: 'stable' },
  declining: { label: '衰退', color: '#f59e0b', badgeVariant: 'declining' },
  archived: { label: '归档', color: '#ef4444', badgeVariant: 'archived' },
  // rejected 复用 candidate 灰色 — 语义上同为"未上线"，仅 Badge 透明度区分
  rejected: { label: '驳回', color: '#71717a', badgeVariant: 'candidate' },
}

// ===== Utilities =====

/** 暗色模式判定 — 跟随 documentElement 上的 .dark 类 */
function isDarkMode(): boolean {
  return document.documentElement.classList.contains('dark')
}

/** 监听暗色模式切换 — class 变化时回调，用于刷新 ECharts 颜色 */
function useDarkModeChange(onChange: () => void) {
  useEffect(() => {
    const el = document.documentElement
    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.attributeName === 'class') {
          onChange()
          return
        }
      }
    })
    observer.observe(el, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [onChange])
}

// ===== Sparkline =====

function Sparkline({
  data,
  color,
  width = 72,
  height = 20,
}: {
  data: number[]
  color: string
  width?: number
  height?: number
}) {
  // SVG polyline — 比 ECharts 实例轻量百倍，20 个 sparkline 同屏无压力
  const max = Math.max(...data)
  const min = Math.min(...data)
  const range = max - min || 1
  const stepX = width / (data.length - 1)
  const points = data
    .map((v, i) => `${(i * stepX).toFixed(2)},${(height - ((v - min) / range) * height).toFixed(2)}`)
    .join(' ')
  return (
    <svg width={width} height={height} className="overflow-visible shrink-0" aria-hidden>
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={1.25}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

// ===== TrendChart (ECharts) =====

function TrendChart({ skills, dates }: { skills: TrendSkill[]; dates: string[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  // 初始化 ECharts 实例（仅一次）— 容器尺寸为 0 时 init 会触发 "Can't get DOM width or height" 警告
  // 并导致 canvas 不渲染。处理：init 时传入实际尺寸（0 则交由 undefined 走默认），并在 rAF 中再 resize 一次
  useLayoutEffect(() => {
    if (!containerRef.current) return
    const el = containerRef.current
    const { width, height } = el.getBoundingClientRect()
    const chart = echarts.init(el, undefined, {
      renderer: 'canvas',
      width: width || undefined,
      height: height || undefined,
    })
    chartRef.current = chart
    requestAnimationFrame(() => chartRef.current?.resize())
    return () => {
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  // 容器尺寸变化 → resize
  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver(() => chartRef.current?.resize())
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  // 数据变化 → setOption
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    chart.setOption(buildTrendOption(skills, dates, isDarkMode()), { replaceMerge: ['series'] })
  }, [skills, dates])

  // 暗色模式切换 → 重新 setOption 刷新所有颜色
  useDarkModeChange(() => {
    chartRef.current?.setOption(buildTrendOption(skills, dates, isDarkMode()), { replaceMerge: ['series'] })
  })

  return <div ref={containerRef} className="h-[360px] w-full" />
}

function buildTrendOption(
  skills: TrendSkill[],
  dates: string[],
  dark: boolean,
): echarts.EChartsCoreOption {
  const textColor = dark ? '#fafafa' : '#09090b'
  const mutedColor = dark ? '#a1a1aa' : '#71717a'
  const borderColor = dark ? '#27272a' : '#e4e4e7'
  const TONE_COLOR: Record<TrendTone, string> = {
    emerging: '#10b981',
    declining: '#f59e0b',
    stable: '#3b82f6',
  }

  return {
    backgroundColor: 'transparent',
    color: skills.map((s) => TONE_COLOR[s.tone]),
    tooltip: {
      trigger: 'axis',
      backgroundColor: dark ? '#18181b' : '#ffffff',
      borderColor: dark ? '#3f3f46' : '#d4d4d8',
      borderWidth: 1,
      textStyle: { color: textColor, fontSize: 12 },
    },
    legend: {
      data: skills.map((s) => s.name),
      textStyle: { color: mutedColor, fontSize: 12 },
      icon: 'roundRect',
      itemWidth: 14,
      itemHeight: 4,
      top: 0,
    },
    grid: { left: 48, right: 56, top: 36, bottom: 28 },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: borderColor } },
      axisLabel: { color: mutedColor, fontSize: 11, interval: 14 },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value',
        name: 'JD 频次',
        nameTextStyle: { color: mutedColor, fontSize: 11 },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: mutedColor, fontSize: 11 },
        splitLine: { lineStyle: { color: borderColor, type: 'dashed' } },
      },
      // 右轴 z-score — 隐藏刻度，仅用于承载阈值 markLine
      // 这样 markLine 的 yAxis 值（2.0 / -1.5）按 z-score 语义定位，不与频次轴混淆
      {
        type: 'value',
        name: 'z-score',
        min: -3,
        max: 3,
        show: false,
      },
    ],
    series: [
      ...skills.map((s) => ({
        name: s.name,
        type: 'line' as const,
        smooth: true,
        symbol: 'circle',
        symbolSize: 6,
        showSymbol: false,
        yAxisIndex: 0,
        data: s.freq,
        endLabel: {
          show: true,
          color: TONE_COLOR[s.tone],
          fontSize: 11,
          formatter: '{a}',
        },
        lineStyle: { width: 2 },
        emphasis: { focus: 'series' as const },
      })),
      // 不可见辅助系列 — 承载 z-score 阈值线，yAxisIndex 指向右轴
      {
        name: '_thresholds',
        type: 'line' as const,
        data: [],
        yAxisIndex: 1,
        markLine: {
          silent: true,
          symbol: 'none',
          data: [
            {
              yAxis: 2.0,
              label: {
                formatter: 'emerging  z=2.0',
                color: '#10b981',
                fontSize: 10,
                position: 'insideEndTop',
              },
              lineStyle: { color: '#10b981', type: 'dashed', width: 1 },
            },
            {
              yAxis: -1.5,
              label: {
                formatter: 'declining  z=-1.5',
                color: '#f59e0b',
                fontSize: 10,
                position: 'insideEndBottom',
              },
              lineStyle: { color: '#f59e0b', type: 'dashed', width: 1 },
            },
          ],
        },
      },
    ],
  }
}

// ===== MetricCard =====

function MetricCard({ metric }: { metric: MetricItem }) {
  const toneColor =
    metric.tone === 'emerging'
      ? 'text-state-emerging'
      : metric.tone === 'declining'
        ? 'text-state-declining'
        : 'text-state-stable'
  const toneBg =
    metric.tone === 'emerging'
      ? 'bg-state-emerging/10'
      : metric.tone === 'declining'
        ? 'bg-state-declining/10'
        : 'bg-state-stable/10'

  // delta 方向仅决定箭头朝向，颜色始终跟随 tone（指标身份色）
  const DeltaIcon = metric.delta > 0 ? ArrowUpRight : metric.delta < 0 ? ArrowDownRight : Minus

  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-ink-muted">{metric.label}</span>
          <span className={cn('inline-flex items-center gap-0.5 text-xs font-mono', toneColor)}>
            <DeltaIcon className="size-3" />
            {metric.delta > 0 ? '+' : ''}{metric.delta}
          </span>
        </div>
        <div className="text-2xl font-semibold tracking-tight tabular-nums">
          {typeof metric.value === 'number' ? (
            metric.value.toLocaleString()
          ) : (
            <span className="font-mono">{metric.value}</span>
          )}
        </div>
        <div className="text-[10px] text-ink-faint mt-1 truncate">{metric.hint}</div>
        <div className={cn('mt-2 h-0.5 rounded-full', toneBg)} />
      </CardContent>
    </Card>
  )
}

// ===== SignalTop10List =====

function SignalTop10List({
  title,
  tone,
  items,
}: {
  title: string
  tone: 'emerging' | 'declining'
  items: SignalSkill[]
}) {
  const color = tone === 'emerging' ? '#10b981' : '#f59e0b'
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center justify-between text-sm">
          <span>{title}</span>
          <Badge variant={tone === 'emerging' ? 'emerging' : 'declining'} className="font-mono">
            {items.length} 信号
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-1 pt-0">
        {items.map((item, idx) => (
          <div
            key={item.name}
            className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-subtle"
          >
            <span className="w-5 text-xs font-mono text-ink-faint tabular-nums">{idx + 1}</span>
            <div className="flex-1 min-w-0">
              <div className="flex items-baseline gap-2">
                <span className="text-sm text-ink truncate">{item.name}</span>
                <span className="text-[10px] font-mono tabular-nums" style={{ color }}>
                  z={item.zScore.toFixed(2)}
                </span>
              </div>
              <div className="text-[10px] text-ink-faint font-mono tabular-nums">
                频次 {item.freq} · MoM {item.mom > 0 ? '+' : ''}{item.mom}%
              </div>
            </div>
            <Sparkline data={item.sparkline} color={color} />
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

// ===== VersionDiffView =====

function VersionDiffView() {
  const [v1, setV1] = useState<string>('v20260729')
  const [v2, setV2] = useState<string>('v20260728')

  // mock 仅 v20260729 vs v20260728 一对真实 diff；其他组合显示占位提示
  const isMockPair = v1 === 'v20260729' && v2 === 'v20260728'
  const diff = useMemo(() => {
    if (!isMockPair) return null
    return {
      added: MOCK_VERSION_DIFF.filter((d) => d.change === 'added'),
      removed: MOCK_VERSION_DIFF.filter((d) => d.change === 'removed'),
      changed: MOCK_VERSION_DIFF.filter((d) => d.change === 'changed'),
    }
  }, [isMockPair])

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <span className="flex items-center gap-2">
            <GitBranch className="size-4" />
            <span>版本快照对比</span>
          </span>
          <div className="flex items-center gap-1.5">
            <Select value={v1} onValueChange={setV1}>
              <SelectTrigger className="h-8 w-[130px] font-mono text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MOCK_VERSIONS.map((v) => (
                  <SelectItem key={v} value={v} className="font-mono text-xs">
                    {v}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-xs text-ink-faint">vs</span>
            <Select value={v2} onValueChange={setV2}>
              <SelectTrigger className="h-8 w-[130px] font-mono text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MOCK_VERSIONS.map((v) => (
                  <SelectItem key={v} value={v} className="font-mono text-xs">
                    {v}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {!diff ? (
          <div className="py-10 text-center text-xs text-ink-muted">
            暂无该版本对比数据
          </div>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-3 mb-4">
              <StatTile label="新增节点" count={diff.added.length} tone="emerging" />
              <StatTile label="删除节点" count={diff.removed.length} tone="declining" />
              <StatTile label="变化节点" count={diff.changed.length} tone="stable" />
            </div>
            <Tabs defaultValue="added">
              <TabsList>
                <TabsTrigger value="added" className="text-xs">新增 ({diff.added.length})</TabsTrigger>
                <TabsTrigger value="removed" className="text-xs">删除 ({diff.removed.length})</TabsTrigger>
                <TabsTrigger value="changed" className="text-xs">变化 ({diff.changed.length})</TabsTrigger>
              </TabsList>
              <TabsContent value="added">
                <DiffTable items={diff.added} />
              </TabsContent>
              <TabsContent value="removed">
                <DiffTable items={diff.removed} />
              </TabsContent>
              <TabsContent value="changed">
                <DiffTable items={diff.changed} />
              </TabsContent>
            </Tabs>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function StatTile({ label, count, tone }: { label: string; count: number; tone: TrendTone }) {
  const color =
    tone === 'emerging'
      ? 'text-state-emerging'
      : tone === 'declining'
        ? 'text-state-declining'
        : 'text-state-stable'
  return (
    <div className="rounded-md border border-border p-3 bg-subtle/40">
      <div className={cn('text-2xl font-semibold tabular-nums', color)}>{count}</div>
      <div className="text-xs text-ink-muted mt-0.5">{label}</div>
    </div>
  )
}

function DiffTable({ items }: { items: VersionDiffItem[] }) {
  if (items.length === 0) {
    return <div className="py-6 text-center text-xs text-ink-faint">无数据</div>
  }
  const typeLabel: Record<VersionDiffItem['type'], string> = {
    position: '岗位',
    skill: '技能',
    evidence: '证据',
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[180px]">节点名</TableHead>
          <TableHead className="w-[60px]">类型</TableHead>
          <TableHead>变化说明</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => (
          <TableRow key={item.name + item.change}>
            <TableCell className="font-medium text-ink">{item.name}</TableCell>
            <TableCell>
              <Badge variant="outline" className="text-[10px] font-mono">
                {typeLabel[item.type]}
              </Badge>
            </TableCell>
            <TableCell className="text-xs text-ink-muted">{item.detail}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

// ===== StateMachineView =====

function StateMachineView() {
  const total = MOCK_STATE_DISTRIBUTION.reduce((sum, s) => sum + s.count, 0)
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center justify-between text-sm">
          <span className="flex items-center gap-2">
            <Calendar className="size-4" />
            <span>岗位状态机流转</span>
          </span>
          <Badge variant="outline" className="font-mono text-xs">
            总计 {total}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0 space-y-5">
        {/* 六状态分布 — 卡片 + 进度条 */}
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          {MOCK_STATE_DISTRIBUTION.map((s) => {
            const meta = STATUS_META[s.status]
            const pct = (s.count / total) * 100
            return (
              <div key={s.status} className="rounded-md border border-border p-3">
                <div className="flex items-center gap-1.5 mb-1.5">
                  <span className="size-2 rounded-full" style={{ backgroundColor: meta.color }} />
                  <span className="text-xs text-ink-muted">{meta.label}</span>
                </div>
                <div className="text-xl font-semibold tabular-nums">{s.count}</div>
                <div className="text-[10px] text-ink-faint font-mono tabular-nums">{pct.toFixed(1)}%</div>
                <div className="mt-2 h-0.5 rounded-full bg-elevated overflow-hidden">
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${pct}%`, backgroundColor: meta.color }}
                  />
                </div>
              </div>
            )
          })}
        </div>

        {/* 最近 7 天状态流转记录 */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-xs font-medium text-ink-secondary">最近 7 天流转记录</h4>
            <span className="text-[10px] text-ink-faint">自动 + 人工审核</span>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[100px]">日期</TableHead>
                <TableHead>岗位</TableHead>
                <TableHead className="w-[180px]">流转</TableHead>
                <TableHead className="w-[60px]">数量</TableHead>
                <TableHead className="w-[80px]">触发</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {MOCK_TRANSITIONS.map((t, idx) => (
                <TableRow key={idx}>
                  <TableCell className="font-mono text-xs text-ink-muted">{t.date}</TableCell>
                  <TableCell className="text-sm text-ink">{t.positionName}</TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1.5">
                      <Badge variant={STATUS_META[t.from].badgeVariant} className="text-[10px]">
                        {STATUS_META[t.from].label}
                      </Badge>
                      <span className="text-ink-faint">→</span>
                      <Badge variant={STATUS_META[t.to].badgeVariant} className="text-[10px]">
                        {STATUS_META[t.to].label}
                      </Badge>
                    </div>
                  </TableCell>
                  <TableCell className="font-mono tabular-nums">{t.count}</TableCell>
                  <TableCell>
                    <Badge
                      variant={t.trigger === 'auto' ? 'stable' : 'outline'}
                      className="text-[10px] font-mono"
                    >
                      {t.trigger === 'auto' ? '自动' : '人工'}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  )
}

// ===== Page =====

export function EvolutionPage() {
  return (
    <>
      <PageHeader
        title="演化看板"
        description="90 天滑动窗口追踪技能频次变化，Z-score 检测新兴/衰退信号 · 岗位六状态机生命周期管理"
        actions={
          <Badge variant="outline" className="font-mono text-xs">
            <Calendar className="size-3 mr-1" />
            T+1 05:00 发布
          </Badge>
        }
      />

      {/* 顶部指标卡 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
        {MOCK_METRICS.map((m) => (
          <MetricCard key={m.key} metric={m} />
        ))}
      </div>

      {/* 技能频次趋势 */}
      <Card className="mb-4">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center justify-between text-sm">
            <span>技能频次趋势 · 最近 90 天</span>
            <span className="text-[10px] font-normal text-ink-faint font-mono">
              Z-score 阈值：emerging 2.0 / declining -1.5
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <TrendChart skills={MOCK_TREND_SKILLS} dates={MOCK_TREND_DATES} />
        </CardContent>
      </Card>

      {/* 新兴 / 衰退 Top-10 双栏 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <SignalTop10List title="新兴技能 Top-10" tone="emerging" items={MOCK_EMERGING} />
        <SignalTop10List title="衰退技能 Top-10" tone="declining" items={MOCK_DECLINING} />
      </div>

      {/* 版本快照对比 */}
      <div className="mb-4">
        <VersionDiffView />
      </div>

      {/* 岗位状态机流转 */}
      <StateMachineView />
    </>
  )
}
