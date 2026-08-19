/**
 * ECharts 2D 力导向图组件 — 简化版
 *
 * 保留能力：
 * - 力导向布局 + 原生 roam/拖拽
 * - 悬停 Focus+Context（emphasis.focus: 'adjacency'）
 * - 左上角悬浮权重过滤面板
 * - 单击选中 / 双击展开 / 空白取消
 * - dispatchAction 选中高亮（不重绘布局）
 */
import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { GraphData, GraphNode, NodeDetail, NodeType, PositionStatus } from './types'
import type { EChartsModel } from './graph-layout'
import { COLOR_BY_STATUS, skillLabelThreshold } from './graph-utils'
import { GraphFilterPanel } from './graph-filter-panel'
import { escapeHtml, isDark } from '@/lib/utils'

/** ECharts 回调参数最小类型 */
interface EChartsParam {
  dataType?: string
  data?: Record<string, unknown>
  value?: unknown
  name?: string
}

echarts.use([GraphChart, TooltipComponent, CanvasRenderer])

interface Graph2DProps {
  data: GraphData
  selectedId?: string | null
  expandedPositions?: Set<string>
  focusRequest?: { id: string; ts: number } | null
  onSelectNode: (node: NodeDetail | null) => void
  onTogglePosition: (id: string) => void
  className?: string
}

export interface Graph2DHandle {
  focusNode: (id: string) => void
  resetView: () => void
}

const SYMBOL_BY_TYPE: Record<Exclude<NodeType, 'position'>, string> = {
  skill: 'circle',
  evidence: 'diamond',
}

const SYMBOL_BY_STATUS: Record<PositionStatus, string> = {
  active: 'circle',
  candidate: 'circle',
  emerging: 'triangle',
  stable: 'circle',
  declining: 'rect',
  archived: 'roundRect',
}

const COLOR_SKILL_LIGHT = '#09090b'
const COLOR_SKILL_DARK = '#fafafa'
const COLOR_EVIDENCE = '#a1a1aa'

function symbolOf(node: GraphNode): string {
  if (node.type === 'position') return SYMBOL_BY_STATUS[node.status ?? 'candidate']
  return SYMBOL_BY_TYPE[node.type]
}

function colorOf(node: GraphNode, dark: boolean): string {
  if (node.type === 'position') return COLOR_BY_STATUS[node.status ?? 'candidate']
  if (node.type === 'skill') return dark ? COLOR_SKILL_DARK : COLOR_SKILL_LIGHT
  return COLOR_EVIDENCE
}

function sizeOf(node: GraphNode, displayValue?: number): number {
  const v = displayValue ?? node.value ?? 30
  const base = node.type === 'position' ? 36 : node.type === 'skill' ? 20 : 15
  const scaled = base + (v / 100) * 20
  return Math.min(56, Math.max(16, scaled))
}

function weightToWidth(weight?: number): number {
  if (!weight) return 1
  return 0.5 + weight * 2.5
}

function isNarrowScreen(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(max-width: 639px)').matches
}

function hexToRgba(hex: string, alpha: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex)
  if (!m) return hex
  const n = parseInt(m[1], 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`
}

export const Graph2D = forwardRef<Graph2DHandle, Graph2DProps>(function Graph2D(
  { data, selectedId, expandedPositions, focusRequest, onSelectNode, onTogglePosition, className },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const [themeVersion, setThemeVersion] = useState(0)
  const [isNarrow, setIsNarrow] = useState(() => isNarrowScreen())
  const [minWeight, setMinWeight] = useState(0)
  // B2: 隐藏的岗位状态集合（空集 = 全显示）
  const [hiddenStatuses, setHiddenStatuses] = useState<Set<import('./types').PositionStatus>>(() => new Set())
  // B2: 仅显示 must（必备）边
  const [showOnlyMustEdges, setShowOnlyMustEdges] = useState(false)

  const toggleStatus = useCallback((s: import('./types').PositionStatus) => {
    setHiddenStatuses((prev) => {
      const next = new Set(prev)
      if (next.has(s)) next.delete(s)
      else next.add(s)
      return next
    })
  }, [])

  const filteredData = useMemo(() => {
    const allowed = new Set<string>()
    const nodes = data.nodes.filter((n) => {
      const w = n.value ?? 0
      if (w < minWeight) return false
      // B2: 隐藏指定状态的岗位节点
      if (n.type === 'position' && n.status && hiddenStatuses.has(n.status as import('./types').PositionStatus)) return false
      allowed.add(n.id)
      return true
    })
    const edges = data.edges.filter((e) => {
      if (!allowed.has(e.source) || !allowed.has(e.target)) return false
      // B2: 仅保留 must 边
      if (showOnlyMustEdges && e.necessity !== 'must') return false
      return true
    })
    return {
      ...data,
      nodes,
      edges,
      stats: { ...data.stats, returnedNodes: nodes.length, totalEdges: edges.length },
    }
  }, [data, minWeight, hiddenStatuses, showOnlyMustEdges])

  const resetView = useCallback(() => {
    const chart = chartRef.current
    if (!chart) return
    chart.setOption({ series: [{ center: ['50%', '50%'], zoom: 1 }] })
  }, [])

  const focusNode = useCallback(
    (id: string) => {
      const chart = chartRef.current
      if (!chart) return
      const node = data.nodes.find((n) => n.id === id)
      if (!node) return
      const seriesModel = (chart as unknown as { getModel(): EChartsModel }).getModel().getSeriesByIndex(0)
      const list = seriesModel?.getData()
      if (!list) return
      const idx = list.indexOfName(node.name)
      if (idx < 0) return
      const layout = list.getItemLayout(idx)
      if (!layout || layout.length < 2) return
      const [x, y] = layout
      const W = chart.getWidth()
      const H = chart.getHeight()
      const targetZoom = 2.4
      const centerX = 0.5 - (x * targetZoom) / W
      const centerY = 0.5 - (y * targetZoom) / H
      chart.setOption({
        series: [{ zoom: targetZoom, center: [centerX, centerY], animationDurationUpdate: 0 }],
      })
    },
    [data.nodes],
  )

  useImperativeHandle(ref, () => ({ focusNode, resetView }), [focusNode, resetView])

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

    chart.on('click', (params) => {
      if (params.dataType === 'node' && params.data) {
        const d = params.data as GraphNode & { displayValue?: number }
        onSelectNode({
          id: d.id,
          name: d.name,
          type: d.type,
          status: d.status,
          value: d.displayValue ?? d.value,
        })
      }
    })

    chart.on('dblclick', (params) => {
      if (params.dataType === 'node' && params.data) {
        const d = params.data as GraphNode
        if (d.type === 'position') onTogglePosition(d.id)
      }
    })

    chart.getZr().on('dblclick', (params) => {
      if (!params.target) resetView()
    })

    chart.getZr().on('click', (params) => {
      if (!params.target) onSelectNode(null)
    })

    requestAnimationFrame(() => chartRef.current?.resize())

    return () => {
      chart.dispose()
      chartRef.current = null
    }
  }, [onSelectNode, onTogglePosition, resetView])

  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver(() => chartRef.current?.resize())
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

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
    const mq = window.matchMedia('(max-width: 639px)')
    const onChange = () => setIsNarrow(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    const dark = isDark()
    const textColor = dark ? '#fafafa' : '#09090b'
    const mutedColor = dark ? '#a1a1aa' : '#71717a'
    const borderColor = dark ? '#27272a' : '#e4e4e7'
    const labelThreshold = skillLabelThreshold(filteredData.nodes)

    const nodes = filteredData.nodes.map((n) => ({
      ...n,
      value: n.type === 'position' ? 1000 : (n.value ?? 0),
      displayValue: n.value,
      symbol: symbolOf(n),
      symbolSize: sizeOf(n, n.value),
      category: n.type,
      itemStyle: {
        color: colorOf(n, dark),
        borderColor,
        borderWidth: 1,
        opacity: 0.95,
        ...(n.type === 'position' && expandedPositions?.has(n.id)
          ? {
              borderColor: dark ? '#fafafa' : '#ffffff',
              borderWidth: 3,
              shadowBlur: isNarrow ? 14 : 22,
              shadowColor: hexToRgba(colorOf(n, dark), 0.55),
            }
          : {}),
      },
      label: {
        show: n.type === 'position' || (n.type === 'skill' && (n.value ?? 0) >= labelThreshold),
        position: 'right',
        color: textColor,
        fontSize: 11,
        fontWeight: n.type === 'position' ? 600 : 400,
        backgroundColor: dark ? 'rgba(24,24,27,0.65)' : 'rgba(255,255,255,0.7)',
        borderRadius: 4,
        padding: [2, 6],
        formatter: n.type === 'position' ? `{a|${n.name}}` : n.name,
        rich: {
          a: { fontWeight: 600, fontSize: 12 },
        },
      },
      emphasis: {
        focus: 'adjacency',
        blurScope: 'coordinateSystem',
        itemStyle: {
          shadowBlur: 24,
          shadowColor: colorOf(n, dark),
        },
        label: { show: true },
      },
    }))

    // C1: 深色模式提升边对比度（#27272a×0.3 → #52525b×0.45，WCAG AA）
    // C1: must（必备）实线全宽；nice（加分）虚线 60% 宽度，视觉区分两种边关系
    const links = filteredData.edges.map((e) => {
      const isMust = e.necessity !== 'nice'
      return {
        source: e.source,
        target: e.target,
        value: e.weight,
        lineStyle: {
          width: isMust ? weightToWidth(e.weight) : Math.max(0.5, weightToWidth(e.weight) * 0.6),
          type: isMust ? 'solid' : 'dashed',
          color: dark ? '#52525b' : borderColor,
          opacity: dark ? 0.45 : 0.3,
          curveness: 0,
        },
        emphasis: {
          lineStyle: {
            opacity: 0.95,
            width: weightToWidth(e.weight) * 1.8,
            color: isMust ? '#3b82f6' : '#10b981',
          },
        },
      }
    })

    const option: echarts.EChartsCoreOption = {
      backgroundColor: 'transparent',
      legend: {
        bottom: 8,
        left: 'center',
        itemWidth: 14,
        itemHeight: 10,
        itemGap: 16,
        textStyle: { color: mutedColor, fontSize: 11 },
        data: ['position', 'skill', 'evidence'],
        formatter: (name: string) => (name === 'position' ? '岗位' : name === 'skill' ? '技能' : '证据'),
      },
      tooltip: {
        trigger: 'item',
        backgroundColor: dark ? '#18181b' : '#ffffff',
        borderColor: dark ? '#3f3f46' : '#d4d4d8',
        borderWidth: 1,
        textStyle: { color: textColor, fontSize: 12 },
        formatter: (params: EChartsParam) => {
          if (params.dataType !== 'node' || !params.data) return ''
          const d = params.data as unknown as GraphNode & { displayValue?: number }
          const lines: string[] = [`<b>${escapeHtml(d.name)}</b>`]
          lines.push(`类型: ${escapeHtml(d.type)}`)
          if (d.type === 'position' && d.status) lines.push(`状态: ${escapeHtml(d.status)}`)
          const displayValue = d.displayValue ?? d.value
          if (typeof displayValue === 'number') lines.push(`权重: ${displayValue}`)
          const hint = d.type === 'position' ? '单击查看详情 · 双击展开/收起技能' : '单击查看详情'
          lines.push(`<span style="color:${mutedColor};font-size:11px">${hint}</span>`)
          return lines.join('<br/>')
        },
      },
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          cursor: 'pointer',
          center: ['50%', '50%'],
          labelLayout: { hideOverlap: true },
          animation: false,
          animationDuration: 0,
          animationDurationUpdate: 0,
          force: {
            repulsion: isNarrow ? [160, 420] : [320, 1000],
            edgeLength: isNarrow ? [70, 150] : [140, 300],
            gravity: isNarrow ? 0.16 : 0.092,
            friction: 0.2,
            layoutAnimation: true,
          },
          scaleLimit: { min: 0.2, max: 5 },
          emphasis: {
            focus: 'adjacency',
            blurScope: 'coordinateSystem',
          },
          selectedMode: 'single',
          select: {
            itemStyle: {
              borderColor: textColor,
              borderWidth: 3,
              shadowBlur: 12,
              shadowColor: (params: EChartsParam) => colorOf(params.data as unknown as GraphNode, dark),
            },
            label: { show: true, color: textColor, fontSize: 11 },
          },
          data: nodes,
          links,
          lineStyle: { opacity: 0.3, curveness: 0 },
          categories: [
            { name: 'position', itemStyle: { color: '#3b82f6' } },
            { name: 'skill', itemStyle: { color: dark ? '#fafafa' : '#09090b' } },
            { name: 'evidence', itemStyle: { color: '#a1a1aa' } },
          ],
        },
      ],
    }

    chart.setOption(option)
  }, [filteredData, themeVersion, expandedPositions, isNarrow])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    if (selectedId) {
      const idx = filteredData.nodes.findIndex((n) => n.id === selectedId)
      if (idx >= 0) {
        chart.dispatchAction({ type: 'select', seriesIndex: 0, dataIndex: idx })
      }
    } else {
      chart.dispatchAction({ type: 'unselect', seriesIndex: 0 })
    }
  }, [selectedId, filteredData.nodes])

  useEffect(() => {
    if (focusRequest) focusNode(focusRequest.id)
  }, [focusRequest, focusNode])

  return (
    <div className={`relative h-full w-full ${className ?? ''}`}>
      {/* B2: 传入岗位状态过滤 + must/nice 边过滤 props */}
      <GraphFilterPanel
        minWeight={minWeight}
        onMinWeightChange={setMinWeight}
        hiddenStatuses={hiddenStatuses}
        onToggleStatus={toggleStatus}
        showOnlyMustEdges={showOnlyMustEdges}
        onToggleMustEdges={setShowOnlyMustEdges}
      />
      <div ref={containerRef} className="h-full w-full" />
    </div>
  )
})
