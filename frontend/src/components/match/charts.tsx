/**
 * 匹配可视化图表组件 — 设计文档 §10.4 + §10.6
 *
 * 4 种 ECharts 图表：
 * - ScoreRing：环形图（总分 0-1 可视化）
 * - RadarChart：雷达图（五维候选人 vs 岗位对比）
 * - SkillHeatmap：热力图（技能矩阵 candidate × required 熟练度）
 * - GanttChart：甘特图（学习路径时间轴）
 *
 * 共用：暗色模式跟随 + 容器尺寸 0 自愈 + 按需导入
 */
import { useEffect, useLayoutEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { GaugeChart, RadarChart as ERadar, HeatmapChart, BarChart } from 'echarts/charts'
import {
  TooltipComponent,
  GridComponent,
  LegendComponent,
  RadarComponent,
  VisualMapComponent,
  DataZoomComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { LearningPathItem, RadarDimension, SkillMatrixItem } from './types'

echarts.use([
  GaugeChart,
  ERadar,
  HeatmapChart,
  BarChart,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  RadarComponent,
  VisualMapComponent,
  DataZoomComponent,
  CanvasRenderer,
])

/** 暗色模式判定 */
function isDark(): boolean {
  return document.documentElement.classList.contains('dark')
}

/** 通用 hook：创建 ECharts 实例 + 容器 0 自愈 + 暗色监听 + 重新渲染触发器 */
function useEChart(
  optionBuilder: () => echarts.EChartsCoreOption,
  deps: React.DependencyList,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

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

  // 数据/配置变化 → setOption
  useEffect(() => {
    chartRef.current?.setOption(optionBuilder(), { replaceMerge: ['series'] })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  // 容器尺寸变化
  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver(() => chartRef.current?.resize())
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  // 暗色模式变化 → 重新 setOption 刷新颜色
  useEffect(() => {
    const el = document.documentElement
    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.attributeName === 'class') {
          chartRef.current?.setOption(optionBuilder(), { replaceMerge: ['series'] })
          break
        }
      }
    })
    observer.observe(el, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return containerRef
}

// ============================================================
// 1. 环形图：总分
// ============================================================
interface ScoreRingProps {
  score: number
  label?: string
  className?: string
}

export function ScoreRing({ score, label = '综合得分', className }: ScoreRingProps) {
  const dark = isDark()
  const textColor = dark ? '#fafafa' : '#09090b'
  const mutedColor = dark ? '#a1a1aa' : '#71717a'

  // 按分值选颜色：≥0.8 绿 / ≥0.6 蓝 / ≥0.4 橙 / <0.4 红
  const scoreColor =
    score >= 0.8 ? '#10b981' : score >= 0.6 ? '#3b82f6' : score >= 0.4 ? '#f59e0b' : '#ef4444'

  const ref = useEChart(
    () => ({
      backgroundColor: 'transparent',
      series: [
        {
          type: 'gauge',
          startAngle: 90,
          endAngle: -270,
          radius: '90%',
          pointer: { show: false },
          progress: {
            show: true,
            overlap: false,
            roundCap: true,
            clip: false,
            itemStyle: { color: scoreColor },
          },
          axisLine: {
            lineStyle: {
              width: 14,
              color: [[1, dark ? '#27272a' : '#e4e4e7']],
            },
          },
          splitLine: { show: false },
          axisTick: { show: false },
          axisLabel: { show: false },
          data: [{ value: score * 100 }],
          title: {
            show: true,
            offsetCenter: ['0%', '20%'],
            color: mutedColor,
            fontSize: 12,
          },
          detail: {
            valueAnimation: true,
            offsetCenter: ['0%', '-10%'],
            formatter: '{value}',
            color: textColor,
            fontSize: 28,
            fontWeight: 600,
            fontFamily: 'JetBrains Mono Variable, monospace',
          },
        },
      ],
    }),
    [score, dark],
  )

  return (
    <div className={className}>
      <div ref={ref} className="h-[180px] w-full" />
      <p className="text-center text-xs text-ink-muted -mt-2">{label}</p>
    </div>
  )
}

// ============================================================
// 2. 雷达图：五维对比
// ============================================================
interface RadarChartProps {
  data: RadarDimension[]
  className?: string
}

export function RadarChart({ data, className }: RadarChartProps) {
  const dark = isDark()
  const textColor = dark ? '#fafafa' : '#09090b'
  const mutedColor = dark ? '#a1a1aa' : '#71717a'
  const splitColor = dark ? '#27272a' : '#e4e4e7'

  const ref = useEChart(
    () => ({
      backgroundColor: 'transparent',
      legend: {
        data: ['候选人', '岗位要求'],
        bottom: 0,
        textStyle: { color: mutedColor, fontSize: 11 },
        itemWidth: 14,
        itemHeight: 8,
      },
      radar: {
        indicator: data.map((d) => ({ name: d.name, max: 100 })),
        radius: '62%',
        center: ['50%', '45%'],
        axisName: { color: textColor, fontSize: 11 },
        splitLine: { lineStyle: { color: splitColor } },
        splitArea: { areaStyle: { color: ['transparent'] } },
        axisLine: { lineStyle: { color: splitColor } },
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              value: data.map((d) => d.candidate),
              name: '候选人',
              itemStyle: { color: '#09090b' },
              areaStyle: { color: 'rgba(9, 9, 11, 0.15)' },
              lineStyle: { width: 2 },
            },
            {
              value: data.map((d) => d.required),
              name: '岗位要求',
              itemStyle: { color: '#3b82f6' },
              areaStyle: { color: 'rgba(59, 130, 246, 0.10)' },
              lineStyle: { width: 2, type: 'dashed' },
            },
          ],
        },
      ],
    }),
    [data, dark],
  )

  return <div ref={ref} className={className ?? 'h-[280px] w-full'} />
}

// ============================================================
// 3. 热力图：技能矩阵
// ============================================================
interface SkillHeatmapProps {
  data: SkillMatrixItem[]
  className?: string
}

const LEVEL_LABEL = ['未掌握', '了解', '熟练', '精通']

export function SkillHeatmap({ data, className }: SkillHeatmapProps) {
  const dark = isDark()
  const mutedColor = dark ? '#a1a1aa' : '#71717a'

  // 构造热力图数据：x 轴=技能，y 轴=[候选人, 岗位要求]，值=熟练度
  const skills = data.map((d) => d.skill)
  const categories = ['候选人', '岗位要求']
  const heatData: [number, number, number][] = []
  data.forEach((d, xi) => {
    heatData.push([xi, 0, d.candidate_level])
    heatData.push([xi, 1, d.required_level])
  })

  const ref = useEChart(
    () => ({
      backgroundColor: 'transparent',
      tooltip: {
        formatter: (params: any) => {
          const [xi, yi, val] = params.value as [number, number, number]
          const item = data[xi]
          const who = categories[yi]
          return `<b>${skills[xi]}</b><br/>${who}: ${LEVEL_LABEL[val]}<br/>必要性: ${item.necessity}`
        },
      },
      grid: { left: 70, right: 20, top: 20, bottom: 90 },
      xAxis: {
        type: 'category',
        data: skills,
        axisLabel: { color: mutedColor, fontSize: 10, rotate: 35, interval: 0 },
        axisLine: { lineStyle: { color: dark ? '#27272a' : '#e4e4e7' } },
      },
      yAxis: {
        type: 'category',
        data: categories,
        axisLabel: { color: mutedColor, fontSize: 11 },
        axisLine: { lineStyle: { color: dark ? '#27272a' : '#e4e4e7' } },
      },
      visualMap: {
        min: 0,
        max: 3,
        calculable: false,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        itemWidth: 12,
        itemHeight: 80,
        textStyle: { color: mutedColor, fontSize: 10 },
        inRange: { color: ['#e4e4e7', '#a1a1aa', '#71717a', '#09090b'] },
        text: ['精通', '未掌握'],
      },
      series: [
        {
          type: 'heatmap',
          data: heatData,
          label: {
            show: true,
            formatter: (p: any) => LEVEL_LABEL[p.value[2] as number] ?? '',
            color: (p: any) => (p.value[2] >= 2 ? '#fafafa' : '#09090b'),
            fontSize: 10,
          },
          emphasis: {
            itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.3)' },
          },
        },
      ],
    }),
    [data, dark],
  )

  return <div ref={ref} className={className ?? 'h-[320px] w-full'} />
}

// ============================================================
// 4. 甘特图：学习路径
// ============================================================
interface GanttChartProps {
  data: LearningPathItem[]
  className?: string
}

const PRIORITY_COLOR: Record<string, string> = {
  high: '#ef4444',
  medium: '#f59e0b',
  low: '#71717a',
}

export function GanttChart({ data, className }: GanttChartProps) {
  const dark = isDark()
  const textColor = dark ? '#fafafa' : '#09090b'
  const mutedColor = dark ? '#a1a1aa' : '#71717a'

  // ECharts 无原生甘特图，用横向 bar + 自定义起止实现
  const categories = data.map((d) => d.skill).reverse() // 反转让第一项在最上
  const barData = data.map((d) => ({
    name: d.skill,
    value: [d.start_offset, d.start_offset + d.duration_days],
    itemStyle: { color: PRIORITY_COLOR[d.priority] },
  }))

  const ref = useEChart(
    () => ({
      backgroundColor: 'transparent',
      tooltip: {
        formatter: (params: any) => {
          const item = data.find((d) => d.skill === params.name)
          if (!item) return ''
          const lines = [`<b>${item.skill}</b>`, `时长: ${item.duration_days} 天`, `优先级: ${item.priority}`]
          if (item.prerequisites.length) lines.push(`先修: ${item.prerequisites.join(', ')}`)
          if (item.courses.length) {
            lines.push('<br/>推荐课程:')
            item.courses.forEach((c) => lines.push(`• ${c.title} (${c.platform}, ${c.hours}h)`))
          }
          return lines.join('<br/>')
        },
      },
      grid: { left: 100, right: 30, top: 20, bottom: 40 },
      xAxis: {
        type: 'value',
        name: '天',
        nameTextStyle: { color: mutedColor, fontSize: 10 },
        axisLabel: { color: mutedColor, fontSize: 10, formatter: 'D{value}' },
        axisLine: { lineStyle: { color: dark ? '#27272a' : '#e4e4e7' } },
        splitLine: { lineStyle: { color: dark ? '#27272a' : '#f4f4f5' } },
      },
      yAxis: {
        type: 'category',
        data: categories,
        axisLabel: { color: textColor, fontSize: 11 },
        axisLine: { lineStyle: { color: dark ? '#27272a' : '#e4e4e7' } },
      },
      series: [
        {
          type: 'bar',
          data: barData,
          barWidth: 18,
          barCategoryGap: '20%',
          coordinateSystem: 'cartesian2d',
          encode: { x: [0, 1], y: 0 },
          label: {
            show: true,
            position: 'right',
            formatter: (p: any) => `${p.value[1] - p.value[0]}天`,
            color: mutedColor,
            fontSize: 10,
          },
        },
      ],
    }),
    [data, dark],
  )

  return <div ref={ref} className={className ?? 'h-[280px] w-full'} />
}
