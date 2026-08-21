/**
 * ECharts 2D 力导向图组件 — 简化版
 *
 * 保留能力：
 * - 力导向布局 + 原生 roam/拖拽
 * - 悬停 Focus+Context（emphasis.focus: 'adjacency'）
 * - 左上角悬浮过滤面板：筛选命中项压暗而非剔除（布局与镜头稳定，不重收敛）
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
import { COLOR_BY_STATUS, computeFilterMarks, skillLabelThreshold } from './graph-utils'
import { buildDagGraph, type DagSkillLink, type DagSkillNode } from '@/components/learning/learning-timeline'
import type { LearningPathItem } from '@/components/match/types'
import { GraphFilterPanel } from './graph-filter-panel'
import { escapeHtml, isDark, cn } from '@/lib/utils'

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
  /** 学习路径（提供时启用"宏观 DAG"视图；缺省保持原力导向全局图谱） */
  learningPath?: LearningPathItem[]
  /** 已掌握技能集（DAG 节点灰/蓝/绿编码依据） */
  completedSkills?: string[]
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

// ── 聚光灯 (Focus + Context) 参数（task T1）──────────────────────
// 悬停/选中节点时，背景节点与边透明度压到该值，制造"聚焦当前邻域"的对比，
// 缓解毛线球效应导致的认知过载。0.10 ≈ 仅留极淡的上下文轮廓。
const BLUR_OPACITY = 0.1
// 悬停邻域内边与节点的强调透明度（相对全不透明前的保留度）
const FOCUS_BRIGHTEN = 0.9

// ── 过滤压暗参数 ─────────────────────────────────────────────
// 被过滤节点/边不从 series.data 中剔除（剔除会改变图拓扑，力导向整体重新
// 收敛导致布局跳变），而是压暗为近乎不可见的"星空背景"：节点集合与顺序
// 不变时 ECharts 保留既有布局坐标，筛选只改透明度，镜头与布局完全稳定。
// 压暗项同时置 silent，悬停/点击不再响应（避免对"已隐藏"节点产生交互）。
const FILTER_DIM_OPACITY = 0.08
const FILTER_DIM_EDGE_OPACITY = 0.04

// ── 语义缩放 (LOD) 档位参数（task T1）───────────────────────────
// zoom 级别低于该阈值仅显示岗位标签
const LOD_ZOOM_POSITIONS_ONLY = 0.55
// zoom 级别达到该阈值才同时显示高权重技能标签
const LOD_ZOOM_SKILLS = 1.2
// label 显示的 zoom 档位（0=仅岗位 / 1=岗位+高权技能 / 2=全量）
type LODBand = 0 | 1 | 2

/** zoom 值 → LOD 标签档位（档位边界即 label 显隐切换点） */
function bandOfZoom(zoom: number): LODBand {
  if (zoom < LOD_ZOOM_POSITIONS_ONLY) return 0
  if (zoom < LOD_ZOOM_SKILLS) return 1
  return 2
}

// 宏观 DAG 学习状态编码（task T2）：绿=已掌握 / 蓝=下一步 / 灰=未解锁
const DAG_COLOR_BY_STATUS: Record<string, string> = {
  done: '#22c55e',
  doing: '#2563eb',
  locked: '#a1a1aa',
}
const DAG_STATUS_LABEL: Record<string, string> = {
  done: '已掌握',
  doing: '下一步',
  locked: '未解锁',
}

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

/** 宏观 DAG option：按拓扑层做分层定位 + 状态配色 + 先修有向箭头（lr 布局） */
function buildDagOption(
  dagNodes: DagSkillNode[],
  dagLinks: DagSkillLink[],
  dark: boolean,
  width: number,
  height: number,
): echarts.EChartsCoreOption {
  // 分层：x 由层号决定（左→右），层内 y 均分并居中
  const byLayer = new Map<number, DagSkillNode[]>()
  let maxLayer = 1
  for (const n of dagNodes) {
    if (!byLayer.has(n.layer)) byLayer.set(n.layer, [])
    byLayer.get(n.layer)!.push(n)
    maxLayer = Math.max(maxLayer, n.layer)
  }
  const marginX = 120
  const marginY = 46
  const stepX = (width - marginX * 2) / Math.max(1, maxLayer - 1)
  const pos = new Map<string, [number, number]>()
  for (const [layer, arr] of byLayer) {
    const stepY = Math.min((height - marginY * 2) / Math.max(1, arr.length), 84)
    arr.forEach((n, j) => {
      pos.set(n.id, [marginX + (layer - 1) * stepX, height / 2 + (j - (arr.length - 1) / 2) * stepY])
    })
  }

  const textColor = dark ? '#fafafa' : '#09090b'
  const mutedColor = dark ? '#a1a1aa' : '#71717a'
  const lineColor = dark ? '#52525b' : '#d4d4d8'

  const data = dagNodes.map((n) => {
    const [x, y] = pos.get(n.id) ?? [0, 0]
    return {
      id: n.id,
      name: n.name,
      x,
      y,
      symbolSize: 34,
      category: n.status,
      itemStyle: { color: DAG_COLOR_BY_STATUS[n.status] ?? '#a1a1aa' },
      label: { show: true, position: 'right', color: textColor, fontSize: 11, formatter: n.name },
      emphasis: { focus: 'adjacency' },
    }
  })
  const links = dagLinks.map((l) => ({
    source: l.source,
    target: l.target,
    lineStyle: { color: lineColor, curveness: 0.2 },
    emphasis: { lineStyle: { color: '#3b82f6', width: 2 } },
  }))

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: dark ? '#18181b' : '#ffffff',
      borderColor: dark ? '#3f3f46' : '#d4d4d8',
      textStyle: { color: textColor, fontSize: 12 },
      formatter: (params: EChartsParam) => {
        const d = params.data as { name?: string; category?: string } | undefined
        const label = DAG_STATUS_LABEL[(d?.category ?? '')] ?? ''
        return `<b>${escapeHtml(d?.name ?? '')}</b>${label ? `<br/><span style="color:${mutedColor};font-size:11px">${label}</span>` : ''}`
      },
    },
    legend: {
      bottom: 8,
      left: 'center',
      itemWidth: 14,
      itemHeight: 8,
      itemGap: 16,
      textStyle: { color: mutedColor, fontSize: 11 },
      data: ['done', 'doing', 'locked'],
      formatter: (name: string) => DAG_STATUS_LABEL[name] ?? name,
    },
    series: [
      {
        type: 'graph',
        layout: 'none',
        roam: true,
        draggable: true,
        cursor: 'pointer',
        scaleLimit: { min: 0.4, max: 3 },
        // 有向箭头：先修 → 目标（edgeSymbol 首项 none、末项 arrow）
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: 7,
        data,
        links,
        lineStyle: { curveness: 0.2, opacity: 0.85 },
        labelLayout: { hideOverlap: true },
        categories: [
          { name: 'done', itemStyle: { color: '#22c55e' } },
          { name: 'doing', itemStyle: { color: '#2563eb' } },
          { name: 'locked', itemStyle: { color: '#a1a1aa' } },
        ],
      },
    ],
  }
}

export const Graph2D = forwardRef<Graph2DHandle, Graph2DProps>(function Graph2D(
  {
    data,
    selectedId,
    expandedPositions,
    focusRequest,
    onSelectNode,
    onTogglePosition,
    learningPath,
    completedSkills,
    className,
  },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const [themeVersion, setThemeVersion] = useState(0)
  const [isNarrow, setIsNarrow] = useState(() => isNarrowScreen())
  const [minWeight, setMinWeight] = useState(0)
  // 语义缩放 (LOD)：当前 zoom 档位（0=仅岗位 / 1=岗位+高权技能 / 2=全量）
  const [lodBand, setLodBand] = useState<LODBand>(1)
  // 实况 zoom（roam 过程中高频变化，用 ref 避免每帧 setState；band 变化才触发重渲）
  const zoomRef = useRef(1)
  // B2: 隐藏的岗位状态集合（空集 = 全显示）
  const [hiddenStatuses, setHiddenStatuses] = useState<Set<import('./types').PositionStatus>>(() => new Set())
  // B2: 仅显示 must（必备）边
  const [showOnlyMustEdges, setShowOnlyMustEdges] = useState(false)
  // task T2: 视图模式 — dag=宏观学习路径 DAG；graph=全局力导向图谱（提供 learningPath 时可用）
  const dagEnabled: boolean = !!learningPath && learningPath.length > 0
  const [viewMode, setViewMode] = useState<'graph' | 'dag'>(dagEnabled ? 'dag' : 'graph')
  // 尺寸版本：容器尺寸变化时重排 DAG（layout:'none' 不自动 reposition）
  const [size, setSize] = useState(0)
  const dagData = useMemo(
    () => (learningPath && learningPath.length > 0 ? buildDagGraph(learningPath, completedSkills) : null),
    [learningPath, completedSkills],
  )

  const toggleStatus = useCallback((s: import('./types').PositionStatus) => {
    setHiddenStatuses((prev) => {
      const next = new Set(prev)
      if (next.has(s)) next.delete(s)
      else next.add(s)
      return next
    })
  }, [])

  // 过滤打标（而非剔除）：布局与镜头在筛选过程中保持稳定
  const filterMarks = useMemo(
    () => computeFilterMarks(data.nodes, data.edges, { minWeight, hiddenStatuses, showOnlyMustEdges }),
    [data, minWeight, hiddenStatuses, showOnlyMustEdges],
  )
  // 首次渲染或 DAG↔图谱切换时才把镜头重置回中心；其余重建（主题/LOD/筛选）
  // 不携带 center，保留用户当前视角，避免滑动筛选条时镜头跳回
  const builtRef = useRef(false)
  const prevViewModeRef = useRef<'graph' | 'dag'>(viewMode)

  // 语义缩放 (LOD)：仅当 zoom 跨越档位边界时才更新 band，避免 roam 每帧重绘
  const applyLodBand = useCallback((zoom: number) => {
    const band = bandOfZoom(zoom)
    zoomRef.current = zoom
    setLodBand((prev) => (prev === band ? prev : band))
  }, [])

  const resetView = useCallback(() => {
    const chart = chartRef.current
    if (!chart) return
    chart.setOption({ series: [{ center: ['50%', '50%'], zoom: 1 }] })
    applyLodBand(1)
  }, [applyLodBand])

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
      // 编程式聚焦放大也会改变 zoom 档位——同步 LOD（前端聚焦到 2.4 → 全量标签）
      applyLodBand(targetZoom)
    },
    [data.nodes, applyLodBand],
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

    // ── 语义缩放 (LOD)：监听 roam（平移/缩放）更新标签档位 ──
    // roam 事件参数含 zoom（缩放级别），档位变化时才触发重绘
    const onRoam = (params: unknown) => {
      const zoom = (params as { zoom?: number })?.zoom
      if (typeof zoom === 'number') applyLodBand(zoom)
    }
    chart.on('roam', onRoam)

    chart.getZr().on('click', (params) => {
      if (!params.target) onSelectNode(null)
    })

    requestAnimationFrame(() => chartRef.current?.resize())

    return () => {
      chart.dispose()
      chartRef.current = null
    }
  }, [onSelectNode, onTogglePosition, resetView, applyLodBand])

  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver(() => {
      chartRef.current?.resize()
      setSize((s) => s + 1)
    })
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

    // 镜头保持：仅首次渲染或 DAG↔图谱切换时才重置镜头中心（filterMarks 等
    // 触发的重建不携带 center，用户当前视角不动）
    const resetCamera = !builtRef.current || prevViewModeRef.current !== viewMode
    prevViewModeRef.current = viewMode

    // 宏观 DAG 视图（task T2）：提供 learningPath 且选用 DAG 时，用分层拓扑渲染
    if (dagData && viewMode === 'dag') {
      const W = chart.getWidth() || 640
      const H = chart.getHeight() || 480
      chart.setOption(buildDagOption(dagData.nodes, dagData.links, isDark(), W, H))
      builtRef.current = true
      return
    }

    const dark = isDark()
    const textColor = dark ? '#fafafa' : '#09090b'
    const mutedColor = dark ? '#a1a1aa' : '#71717a'
    const borderColor = dark ? '#27272a' : '#e4e4e7'
    const labelThreshold = skillLabelThreshold(data.nodes)

    const nodes = data.nodes.map((n) => {
      const dimmed = filterMarks.dimNodeIds.has(n.id)
      return {
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
          ...(dimmed ? { opacity: FILTER_DIM_OPACITY } : {}),
        },
        label: {
          // 语义缩放 (LOD)：标签显隐由 zoom 档位驱动
          // - band 0（zoom<0.55）：仅岗位
          // - band 1（0.55≤zoom<1.2）：岗位 + 高权重技能（≥中位阈值）
          // - band 2（zoom≥1.2）：全量（含低权技能）
          show: dimmed
            ? false
            : n.type === 'position'
              ? lodBand >= 0
              : n.type === 'skill'
                ? lodBand === 2 || (lodBand >= 1 && (n.value ?? 0) >= labelThreshold)
                : false,
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
        // 压暗项不响应悬停/点击（星空背景，避免对"已隐藏"节点产生交互）
        silent: dimmed,
        emphasis: {
          focus: 'adjacency',
          blurScope: 'coordinateSystem',
          itemStyle: {
            shadowBlur: 24,
            shadowColor: colorOf(n, dark),
            opacity: FOCUS_BRIGHTEN,
          },
          label: { show: true },
        },
      }
    })

    // C1: 深色模式提升边对比度（#27272a×0.3 → #52525b×0.45，WCAG AA）
    // C1: must（必备）实线全宽；nice（加分）虚线 60% 宽度，视觉区分两种边关系
    const links = data.edges.map((e, i) => {
      const isMust = e.necessity !== 'nice'
      const dimmed = filterMarks.dimEdgeFlags[i]
      return {
        source: e.source,
        target: e.target,
        value: e.weight,
        silent: dimmed,
        lineStyle: {
          width: isMust ? weightToWidth(e.weight) : Math.max(0.5, weightToWidth(e.weight) * 0.6),
          type: isMust ? 'solid' : 'dashed',
          color: dark ? '#52525b' : borderColor,
          opacity: dimmed ? FILTER_DIM_EDGE_OPACITY : dark ? 0.45 : 0.3,
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
          // 镜头保持：仅首建/视图切换时重置中心，其余重建不动当前视角
          ...(resetCamera ? { center: ['50%', '50%'] as [string, string] } : {}),
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
            // 聚光灯 (Focus + Context)：悬停/选中时无关节点与边压到 10% 透明度，
            // 仅保留"当前节点 + 一阶邻居"的清晰对比，缓解毛线球认知过载
            blur: {
              itemStyle: { opacity: BLUR_OPACITY },
              lineStyle: { opacity: BLUR_OPACITY },
              label: { opacity: 0.1 },
            },
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
    builtRef.current = true
  }, [data, filterMarks, themeVersion, expandedPositions, isNarrow, dagData, viewMode, size, lodBand])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    if (selectedId) {
      const idx = data.nodes.findIndex((n) => n.id === selectedId)
      if (idx >= 0) {
        // select 负责选中项描边；highlight 触发 focus:adjacency + blur，使"点击/选中态"
        // 同样把无关节点与边压到 10%（聚光灯对悬停与点击都生效，task T1）
        chart.dispatchAction({ type: 'select', seriesIndex: 0, dataIndex: idx })
        chart.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: idx })
      }
    } else {
      chart.dispatchAction({ type: 'unselect', seriesIndex: 0 })
      chart.dispatchAction({ type: 'downplay', seriesIndex: 0 })
    }
  }, [selectedId, data.nodes])

  useEffect(() => {
    if (focusRequest) focusNode(focusRequest.id)
  }, [focusRequest, focusNode])

  return (
    <div className={`relative h-full w-full ${className ?? ''}`}>
      {/* task T2: 学习路径可用时提供 宏观 DAG / 全局图谱 切换 */}
      {dagEnabled && (
        <div className="absolute right-3 top-3 z-20 flex overflow-hidden rounded-md border border-border bg-canvas/90 shadow-sm text-[10px]">
          {(
            [
              ['dag', '宏观 DAG'],
              ['graph', '全局图谱'],
            ] as const
          ).map(([mode, label]) => (
            <button
              key={mode}
              type="button"
              onClick={() => setViewMode(mode)}
              className={cn(
                'px-2.5 py-1 font-medium transition-colors',
                viewMode === mode ? 'bg-ink text-canvas' : 'text-ink-muted hover:bg-subtle hover:text-ink',
              )}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {/* B2: 传入岗位状态过滤 + must/nice 边过滤 props；压暗式过滤的可见统计 */}
      <GraphFilterPanel
        minWeight={minWeight}
        onMinWeightChange={setMinWeight}
        hiddenStatuses={hiddenStatuses}
        onToggleStatus={toggleStatus}
        showOnlyMustEdges={showOnlyMustEdges}
        onToggleMustEdges={setShowOnlyMustEdges}
        visibleCount={filterMarks.visibleNodes}
        hiddenCount={data.nodes.length - filterMarks.visibleNodes}
      />
      <div ref={containerRef} className="h-full w-full" />
    </div>
  )
})
