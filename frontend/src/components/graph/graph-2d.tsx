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
import type { GraphData, GraphEdge, GraphNode, NodeDetail, NodeType, PositionStatus } from './types'
import type { EChartsModel } from './graph-layout'
import { COLOR_BY_STATUS, computeFilterMarks, isSoftSkill, skillLabelThreshold } from './graph-utils'
import { graphColors, graphNodeColor, GRAPH_OPACITY, skillCategoryColor } from './graph-visual-tokens'
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
  /** 域超节点双击展开/收起（panorama 聚合下钻第二级；缺省域节点不可展开） */
  onToggleDomain?: (id: string) => void
  /** 学习路径（提供时启用"宏观 DAG"视图；缺省保持原力导向全局图谱） */
  learningPath?: LearningPathItem[]
  /** 已掌握技能集（DAG 节点灰/蓝/绿编码依据） */
  completedSkills?: string[]
  /** 演化时间轴标记（P0-2）：本版新增绿环 / 消亡橙虚线（打标不剔除） */
  evolutionMarks?: { addedIds: Set<string>; removedIds: Set<string> } | null
  /** 技能标签 Top-N 白名单（技术栈视图降噪：仅集合内技能在 LOD band 1 常显标签） */
  skillLabelTopIds?: Set<string> | null
  /** 环形布局（技术栈视图专用）：技能按频次顺时针排外圈、岗位聚内圈，边呈放射状。
      固定坐标（layout:'none'）无布局抖动，演示镜头飞行稳定；缺省力导向。 */
  ringLayout?: boolean
  className?: string
}

export interface Graph2DHandle {
  focusNode: (id: string) => void
  resetView: () => void
  /** 演示书签：镜头平滑飞行到指定节点（zoom 缺省 2.4；布局未静止时自动重试） */
  flyTo: (id: string, zoom?: number) => void
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

// ── 演示书签飞行参数 ─────────────────────────────────────────
// 镜头平滑过渡时长（与 3D cameraPosition 的 600ms 对齐，观感一致）
const FLY_DURATION_MS = 600
// 布局未静止时坐标解析的重试上限（每次间隔 250ms）
const FLY_MAX_ATTEMPTS = 3

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
  const theme = dark ? 'dark' : 'light'
  if (node.isDomain) return graphNodeColor(theme, 'domain')
  if (node.type === 'position') return graphNodeColor(theme, 'position', node.status ?? 'candidate')
  if (isSoftSkill(node)) return graphNodeColor(theme, 'softSkill')
  if (node.type === 'skill') {
    // 08-28 技术栈降噪：技能按类目着色（s_category 随 view 接口下发），
    // 未收录类目回落默认技能色
    const catColor = skillCategoryColor(node.skill_category)
    if (catColor) return catColor
    return graphNodeColor(theme, 'skill')
  }
  return graphNodeColor(theme, 'evidence')
}

function sizeOf(node: GraphNode, displayValue?: number): number {
  // 职能域是测绘锚点：比岗位更大，并以成员数编码区域规模。
  if (node.isDomain) return Math.min(78, 48 + (node.memberCount ?? 1) * 1.5)
  const value = displayValue ?? node.value ?? 30
  const base = node.type === 'position' ? 34 : node.type === 'skill' ? 18 : 14
  const scaled = base + (value / 100) * 18
  return Math.min(54, Math.max(14, scaled))
}

/** 单条边的基础视觉（宽/色/线型/透明度）——option 构建与悬停离场复位共用一套口径 */
function edgeBaseStyle(
  edge: GraphEdge,
  nodeById: Map<string, GraphNode>,
  colors: ReturnType<typeof graphColors>,
  dimmed: boolean,
  weightNorm = 0,
) {
  const source = nodeById.get(edge.source)
  const target = nodeById.get(edge.target)
  const touchesDomain = source?.isDomain || target?.isDomain
  const domainToDomain = source?.isDomain && target?.isDomain
  const kind = domainToDomain ? 'shared' : touchesDomain ? 'membership' : edge.necessity !== 'nice' ? 'must' : 'nice'
  const base = kind === 'membership'
    ? { width: 0.8, type: 'dotted' as const, color: colors.edge, curveness: 0.08 }
    : kind === 'shared'
      ? { width: 0.7, type: 'dashed' as const, color: colors.edge, curveness: 0.22 }
      : kind === 'must'
        ? { width: 1.5, type: 'solid' as const, color: colors.edgeStrong, curveness: 0 }
        : { width: 0.9, type: 'dashed' as const, color: colors.edgeOptional, curveness: 0 }
  // 08-28 技术栈降噪：岗位关系边透明度/线宽按权重渐变——低权边压暗到近隐约，
  // 高权边保持可读，悬停邻接提亮仍由 hover 直改机制叠加
  const width =
    kind === 'must' ? 0.7 + 1.1 * weightNorm : kind === 'nice' ? 0.5 + 0.8 * weightNorm : base.width
  return {
    kind,
    ...base,
    width,
    opacity: dimmed
      ? FILTER_DIM_EDGE_OPACITY
      : kind === 'membership' || kind === 'shared'
        ? 0.45
        : 0.08 + 0.42 * weightNorm,
  }
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
    onToggleDomain,
    learningPath,
    completedSkills,
    evolutionMarks,
    skillLabelTopIds,
    ringLayout,
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
  // 软技能压暗开关（与技术栈技能分开查看）
  const [hideSoftSkills, setHideSoftSkills] = useState(false)
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

  const resetFilters = useCallback(() => {
    setMinWeight(0)
    setHiddenStatuses(new Set())
    setShowOnlyMustEdges(false)
    setHideSoftSkills(false)
  }, [])

  // 过滤打标（而非剔除）：布局与镜头在筛选过程中保持稳定
  const filterMarks = useMemo(
    () => computeFilterMarks(data.nodes, data.edges, { minWeight, hiddenStatuses, showOnlyMustEdges, hideSoftSkills }),
    [data, minWeight, hiddenStatuses, showOnlyMustEdges, hideSoftSkills],
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

  /** 解析节点当前布局坐标（节点不存在或布局未就绪返回 null） */
  const resolveNodePoint = useCallback(
    (id: string): [number, number] | null => {
      const chart = chartRef.current
      if (!chart) return null
      const node = data.nodes.find((n) => n.id === id)
      if (!node) return null
      const seriesModel = (chart as unknown as { getModel(): EChartsModel }).getModel().getSeriesByIndex(0)
      const list = seriesModel?.getData()
      if (!list) return null
      const idx = list.indexOfName(node.name)
      if (idx < 0) return null
      const layout = list.getItemLayout(idx)
      if (!layout || layout.length < 2) return null
      return [layout[0], layout[1]]
    },
    [data.nodes],
  )

  // view 坐标系的 center 语义是「缩放锚点的图坐标」（Number 按 pixel 解析，非画布
  // 百分比），锚点会被平移到画布中心——聚焦节点须直接传节点布局坐标。历史坑：
  // 曾按 screen = center%×W + zoom×point 语义换算出 0.x 量级小数传入，被
  // parsePercent 当作 0.x 像素，镜头每次都锚到图原点外，全图被推出画布外。
  const focusNode = useCallback(
    (id: string) => {
      const chart = chartRef.current
      if (!chart) return
      const point = resolveNodePoint(id)
      if (!point) return
      chart.setOption({
        series: [{ zoom: 2.4, center: point, animationDurationUpdate: 0 }],
      })
      // 编程式聚焦放大也会改变 zoom 档位——同步 LOD（前端聚焦到 2.4 → 全量标签）
      applyLodBand(2.4)
      // 定位目标常随岗位/域刚展开上画布，力导向仍在迭代、坐标持续漂移——
      // 800ms 后按最新坐标校正一次镜头（漂移 ≤8px 视为已静止，不重设）
      window.setTimeout(() => {
        const settled = resolveNodePoint(id)
        if (!settled || !chartRef.current) return
        if (Math.abs(settled[0] - point[0]) <= 8 && Math.abs(settled[1] - point[1]) <= 8) return
        chartRef.current.setOption({
          series: [{ zoom: 2.4, center: settled, animationDurationUpdate: 0 }],
        })
      }, 800)
    },
    [resolveNodePoint, applyLodBand],
  )

  // 演示书签飞行：带缓动的镜头过渡（全局 animation:false 需按次临时开启）。
  // 力导向未静止时坐标可能取不到，短间隔重试至多 3 次；LOD 档位在落点后同步，
  // 避免飞行途中触发全量重建打断动画。历史坑：一律 setOption merge，禁用 restore。
  const flyTo = useCallback(
    (id: string, zoom?: number) => {
      const targetZoom = zoom ?? 2.4
      let attempts = 0
      const tryFly = () => {
        const chart = chartRef.current
        if (!chart) return
        const point = resolveNodePoint(id)
        if (point) {
          const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
          const duration = reduceMotion ? 0 : FLY_DURATION_MS
          chart.setOption({
            series: [
              {
                zoom: targetZoom,
                center: point,
                animation: duration > 0,
                animationDurationUpdate: duration,
                animationEasingUpdate: 'cubicInOut',
              },
            ],
          })
          window.setTimeout(() => {
            chartRef.current?.setOption({ series: [{ animation: false, animationDurationUpdate: 0 }] })
            applyLodBand(targetZoom)
          }, duration + 50)
          return
        }
        if (++attempts < FLY_MAX_ATTEMPTS) window.setTimeout(tryFly, 250)
      }
      tryFly()
    },
    [resolveNodePoint, applyLodBand],
  )

  useImperativeHandle(ref, () => ({ focusNode, resetView, flyTo }), [focusNode, resetView, flyTo])

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
          isDomain: d.isDomain,
          memberCount: d.memberCount,
        })
      }
    })

    chart.on('dblclick', (params) => {
      if (params.dataType === 'node' && params.data) {
        const d = params.data as GraphNode
        if (d.isDomain) onToggleDomain?.(d.id)
        else if (d.type === 'position') onTogglePosition(d.id)
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
  }, [onSelectNode, onTogglePosition, onToggleDomain, resetView, applyLodBand])

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
    const colors = graphColors(dark ? 'dark' : 'light')
    const textColor = colors.ink
    const mutedColor = colors.muted
    const borderColor = colors.border
    const labelThreshold = skillLabelThreshold(data.nodes)

    const nodes = data.nodes.map((n) => {
      const dimmed = filterMarks.dimNodeIds.has(n.id)
      return {
        ...n,
        // 斥力权重：域超节点按成员规模锚定聚团（岗位固定 1000 的既有口径不变）
        value: n.isDomain ? Math.min(2000, 600 + (n.memberCount ?? 0) * 30)
          : n.type === 'position' ? 1000 : (n.value ?? 0),
        displayValue: n.value,
        symbol: symbolOf(n),
        symbolSize: sizeOf(n, n.value),
        // 软技能独立 category：图例「软技能」项可单独开关（其余按节点类型）
        category: isSoftSkill(n) ? 'soft' : n.type,
        itemStyle: {
          color: colorOf(n, dark),
          borderColor: n.isDomain ? colors.edgeStrong : borderColor,
          borderWidth: n.isDomain ? 3 : 1,
          opacity: GRAPH_OPACITY.node,
          ...(n.isDomain
            ? {
                shadowBlur: 22,
                shadowColor: hexToRgba(colors.domain, 0.3),
              }
            : {}),
          ...(n.type === 'position' && expandedPositions?.has(n.id)
            ? {
                borderColor: dark ? '#c7d2fe' : '#ffffff',
                borderWidth: 2,
                shadowBlur: isNarrow ? 8 : 12,
                shadowColor: hexToRgba(colorOf(n, dark), 0.38),
              }
            : {}),
          // 待归类桶弱化（P1-2）：虚线描边 + 降透明度，兜底域不与实域抢视觉权重
          ...(n.isUncategorized && !dimmed ? { borderType: 'dashed' as const, borderWidth: 2, opacity: 0.6 } : {}),
          // 演化时间轴打标（P0-2）：本版新增绿环高亮 / 消亡橙虚线（打标不剔除）
          ...(evolutionMarks?.addedIds.has(n.id) && !dimmed
            ? { borderColor: '#22c55e', borderWidth: 3, shadowBlur: 18, shadowColor: 'rgba(34,197,94,0.7)' }
            : {}),
          ...(evolutionMarks?.removedIds.has(n.id) && !dimmed
            ? { borderColor: '#f97316', borderWidth: 2, borderType: 'dashed' as const }
            : {}),
          ...(dimmed ? { opacity: FILTER_DIM_OPACITY } : {}),
        },
        label: {
          // 语义缩放 (LOD)：标签显隐由 zoom 档位驱动
          // - band 0（zoom<0.55）：仅岗位
          // - band 1（0.55≤zoom<1.2）：岗位 + 高权重技能（≥中位阈值）
          // - band 2（zoom≥1.2）：全量（含低权技能）
          // 演化打标节点标签强制显示（不受 LOD 压制——时间轴叙事主角）
          show: dimmed
            ? false
            : evolutionMarks && (evolutionMarks.addedIds.has(n.id) || evolutionMarks.removedIds.has(n.id))
              ? true
              : n.isDomain || n.type === 'position'
                ? lodBand >= 0
                : n.type === 'skill'
                ? lodBand === 2 ||
                  (lodBand >= 1 &&
                    (skillLabelTopIds
                      ? skillLabelTopIds.has(n.id)
                      : (n.value ?? 0) >= labelThreshold))
                : false,
          position: 'right',
          color: textColor,
          fontSize: 11,
          fontWeight: n.isDomain || n.type === 'position' ? 600 : 400,
          backgroundColor: colors.labelSurface,
          borderRadius: 3,
          padding: [2, 5],
          formatter:
            n.isDomain
              ? `{a|${n.name} · ${n.memberCount ?? 0} 岗}`
              : n.type === 'position'
                ? `{a|${n.name}}`
                : n.name,
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

    // 环形布局坐标注入（layout:'none' 直接消费节点 x/y）：
    // 技能按频次降序顺时针铺外圈（半径 420），岗位按关联度降序铺内圈（半径 170），
    // 同圈角间距均分——放射状边从内圈放射到外圈，"技能→服务岗位"读向清晰。
    if (ringLayout) {
      const skills = nodes
        .filter((n) => n.type === 'skill')
        .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
      const positions = nodes
        .filter((n) => n.type !== 'skill')
        .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
      // none 布局数据坐标直接映射视口：原点取画布实际中心（容器像素，实时读取），
      // ECharts series.center 对已有 roam 状态不生效（线上偏移实证）。
      // 容量法排环：每环节点数由"节点直径 + 标签余量"的周长容量决定，
      // 放不下就外扩一层——杜绝固定半径下的节点重叠（线上实证 30 岗挤双环）。
      const rect = containerRef.current?.getBoundingClientRect()
      const W = rect?.width || 900
      const H = rect?.height || 640
      const CX = W / 2
      const CY = H / 2
      const minDim = Math.min(W, H)
      const place = (n: (typeof nodes)[number], radius: number, angle: number) => {
        ;(n as GraphNode & { x?: number; y?: number }).x = CX + radius * Math.cos(angle)
        ;(n as GraphNode & { x?: number; y?: number }).y = CY + radius * Math.sin(angle)
      }
      // 外圈技能：半径用满画布（短边 48%），120 技能节点 18px 在该半径下
      // 弧间距 ≈ 2πR/120 ≈ 17px+ ——仍略挤，标签只留 Top-30（skillLabelTopIds）
      const R_OUTER = minDim * 0.47
      skills.forEach((n, i) => {
        place(n, R_OUTER, (i / Math.max(1, skills.length)) * Math.PI * 2 - Math.PI / 2)
      })
      // 岗位多环：起始环 0.22×短边（给内区留呼吸空间），环间距 84px；
      // 每环按"岗位节点上限 52px + 间距 34px"的周长容量装，装不下外扩一层
      const NODE_GAP = 34
      const POS_NODE_PX = 52 // 岗位节点直径上限（sizeOf 34-52）
      let ringR = Math.max(150, minDim * 0.22)
      let placedInRing = 0
      let ringCapacity = Math.floor((2 * Math.PI * ringR) / (POS_NODE_PX + NODE_GAP))
      positions.forEach((n) => {
        if (placedInRing >= ringCapacity) {
          ringR += 84
          placedInRing = 0
          ringCapacity = Math.floor((2 * Math.PI * ringR) / (34 + NODE_GAP))
        }
        const angle =
          (placedInRing / Math.max(1, ringCapacity)) * Math.PI * 2 +
          (ringR === Math.max(110, minDim * 0.14) ? 0 : Math.PI / ringCapacity)
        place(n, ringR, angle)
        placedInRing += 1
      })
    }

    // 关系分层：域隶属=细虚线，域间共享=弱弧线，must=海图蓝实线，nice=灰蓝虚线。
    // 各类线宽统一固定值（演示口径）；悬停节点的关联边提亮由下方 hover 效果
    // 直改边元素实现，不进 option。
    const nodeById = new Map(data.nodes.map((node) => [node.id, node]))
    const maxWeight = data.edges.reduce((mx, e) => Math.max(mx, e.weight ?? 0), 0)
    const links = data.edges.map((edge, index) => {
      const dimmed = filterMarks.dimEdgeFlags[index]
      const norm = maxWeight > 0 ? Math.min(1, (edge.weight ?? 0) / maxWeight) : 0
      const base = edgeBaseStyle(edge, nodeById, colors, dimmed, norm)
      return {
        source: edge.source,
        target: edge.target,
        value: edge.weight,
        silent: dimmed,
        lineStyle: { width: base.width, type: base.type, color: base.color, curveness: base.curveness, opacity: base.opacity },
        emphasis: { lineStyle: { opacity: 0.95, width: 2.4, color: base.kind === 'nice' ? colors.edgeOptional : colors.edgeStrong } },
      }
    })

    const option: echarts.EChartsCoreOption = {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item',
        backgroundColor: colors.tooltip,
        borderColor: colors.tooltipBorder,
        borderWidth: 1,
        textStyle: { color: textColor, fontSize: 12 },
        formatter: (params: EChartsParam) => {
          if (params.dataType !== 'node' || !params.data) return ''
          const d = params.data as unknown as GraphNode & { displayValue?: number }
          const lines: string[] = [`<b>${escapeHtml(d.name)}</b>`]
          lines.push(`类型: ${escapeHtml(isSoftSkill(d) ? '软技能' : d.type)}`)
          if (d.type === 'position' && d.status) lines.push(`状态: ${escapeHtml(d.status)}`)
          if (evolutionMarks?.addedIds.has(d.id))
            lines.push('<span style="color:#22c55e;font-size:11px">● 本版新增</span>')
          else if (evolutionMarks?.removedIds.has(d.id))
            lines.push('<span style="color:#f97316;font-size:11px">◌ 本版消亡</span>')
          if (d.type === 'skill' && d.skill_category && !isSoftSkill(d)) {
            lines.push(`类目: ${escapeHtml(d.skill_category)}`)
          }
          const displayValue = d.displayValue ?? d.value
          if (typeof displayValue === 'number') lines.push(`权重: ${displayValue}`)
          const hint =
            d.isDomain
              ? `职能域 · ${d.memberCount ?? 0} 个岗位：双击展开/收起`
              : d.type === 'position'
                ? '单击查看详情 · 双击展开/收起技能'
                : '单击查看详情'
          lines.push(`<span style="color:${mutedColor};font-size:11px">${hint}</span>`)
          return lines.join('<br/>')
        },
      },
      series: [
        {
          type: 'graph',
          // 环形布局：固定坐标（技能外圈按频次顺时针 / 岗位内圈），无布局抖动；
          // 其余视图维持力导向（拖拽探索语义）
          layout: ringLayout ? 'none' : 'force',
          roam: true,
          draggable: !ringLayout,
          cursor: 'pointer',
          // 镜头保持：仅首建/视图切换时重置中心，其余重建不动当前视角
          ...(resetCamera ? { center: ['50%', '50%'] as [string, string] } : {}),
          labelLayout: { hideOverlap: true },
          animation: false,
          animationDuration: 0,
          animationDurationUpdate: 0,
          ...(ringLayout
            ? {}
            : {
                force: {
                  repulsion: isNarrow ? [160, 420] : [320, 1000],
                  edgeLength: isNarrow ? [70, 150] : [140, 300],
                  gravity: isNarrow ? 0.16 : 0.092,
                  friction: 0.2,
                  layoutAnimation: true,
                },
              }),
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
            { name: 'position', itemStyle: { color: COLOR_BY_STATUS.candidate } },
            { name: 'skill', itemStyle: { color: colors.skill } },
            { name: 'soft', itemStyle: { color: colors.softSkill } },
            { name: 'evidence', itemStyle: { color: colors.evidence } },
          ],
        },
      ],
    }

    chart.setOption(option)
    builtRef.current = true
  }, [data, filterMarks, themeVersion, expandedPositions, isNarrow, dagData, viewMode, size, lodBand, evolutionMarks])

  // ── 悬停节点 → 关联连线提亮 ──
  // 邻接表（node id → 关联边下标）与基础视觉快照（与 option 构建同口径）：
  // 离场复位、主 option 重建后补涂都依赖后者——重建会换新元素回基础样式
  const incidentEdges = useMemo(() => {
    const m = new Map<string, number[]>()
    data.edges.forEach((edge, i) => {
      for (const id of [edge.source, edge.target]) {
        const arr = m.get(id)
        if (arr) arr.push(i)
        else m.set(id, [i])
      }
    })
    return m
  }, [data])

  // themeVersion 不在函数体内出现但语义必要：主题切换时 isDark() 结果变化，
  // 基础色/透明度需随 memo 重算（否则离场复位会涂错主题的底色）
  const edgeBase = useMemo(() => {
    const nodeById = new Map(data.nodes.map((node) => [node.id, node]))
    const dark = isDark()
    const colors = graphColors(dark ? 'dark' : 'light')
    const maxWeight = data.edges.reduce((mx, e) => Math.max(mx, e.weight ?? 0), 0)
    return data.edges.map((edge, i) => ({
      ...edgeBaseStyle(
        edge, nodeById, colors, !!filterMarks.dimEdgeFlags[i],
        maxWeight > 0 ? Math.min(1, (edge.weight ?? 0) / maxWeight) : 0,
      ),
      dimmed: !!filterMarks.dimEdgeFlags[i],
    }))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- themeVersion 语义必要（isDark 非响应式读取）
  }, [data, filterMarks, themeVersion])

  const hoverNodeRef = useRef<string | null>(null)
  const hoverEdgesRef = useRef<number[] | null>(null)

  useEffect(() => {
    if (viewMode !== 'graph') {
      hoverNodeRef.current = null
      hoverEdgesRef.current = null
      return
    }
    const chart = chartRef.current
    if (!chart) return

    // 直改边图形元素（与新兴脉冲同一模式）：零 data diff、不重启力导向；
    // 不经 setOption，邻域压暗（focus:adjacency 的 blur 状态）不受打扰
    const paintEdges = (idxs: number[], hovered: boolean) => {
      const strong = graphColors(isDark() ? 'dark' : 'light').edgeStrong
      const edgeData = (
        (chart as unknown as { getModel(): EChartsModel }).getModel().getSeriesByIndex(0) as unknown as {
          getEdgeData?: () => {
            getItemGraphicEl: (i: number) => unknown
          }
        }
      )?.getEdgeData?.()
      if (!edgeData) return
      type PaintTarget = {
        style: { lineWidth?: number; stroke?: string; opacity?: number }
        dirty: () => void
      }
      const isPaintable = (x: unknown): x is PaintTarget => {
        const s = (x as { style?: unknown })?.style
        return typeof s === 'object' && s !== null
      }
      for (const i of idxs) {
        const el = edgeData.getItemGraphicEl(i)
        const base = edgeBase[i]
        if (!el || !base) continue
        if (hovered && base.dimmed) continue // 过滤压暗的边不复活
        // edgeSymbol(['none','arrow']) 下边元素是 Group（线 + 箭头子元素），
        // Group 无 style——取含 style 的显示元素（线/箭头）逐个涂色；
        // zrender Group 的 children 是方法（children()），非数组属性
        const anyEl = el as { children?: unknown[] | (() => unknown[]) }
        const kids = typeof anyEl.children === 'function' ? anyEl.children() : (anyEl.children ?? [])
        const targets = isPaintable(el) ? [el] : kids.filter(isPaintable)
        for (const t of targets) {
          t.style.lineWidth = hovered ? 2.4 : base.width
          t.style.stroke = hovered ? strong : base.color
          t.style.opacity = hovered ? 0.95 : base.opacity
          t.dirty()
        }
      }
    }

    const applyHover = (nodeId: string | null) => {
      if (hoverNodeRef.current === nodeId) return
      const prev = hoverEdgesRef.current
      const next = nodeId ? (incidentEdges.get(nodeId) ?? null) : null
      hoverNodeRef.current = nodeId
      hoverEdgesRef.current = next
      if (prev) paintEdges(prev, false)
      if (next) paintEdges(next, true)
    }

    const onGlobalOut = () => applyHover(null)
    chart.on('mouseover', (params) => {
      if (params.dataType === 'node' && params.data) {
        const id = (params.data as GraphNode).id
        if (id) applyHover(id)
      }
    })
    chart.on('mouseout', (params) => {
      if (params.dataType === 'node') applyHover(null)
    })
    chart.getZr().on('globalout', onGlobalOut)

    // 主 option 重建后元素换新回基础样式，按当前悬停状态补一次提亮
    if (hoverEdgesRef.current) paintEdges(hoverEdgesRef.current, true)

    return () => {
      chart.off('mouseover')
      chart.off('mouseout')
      // 卸载序：挂载 effect（定义在前）先 dispose 图表，dispose 后 getZr() 为
      // null——可选链守卫，防 cleanup 阶段把整页打成 Unexpected Application Error
      chart.getZr()?.off('globalout', onGlobalOut)
    }
  }, [viewMode, edgeBase, incidentEdges])

  // 新兴岗位脉冲光晕（视觉评审 P1-3）：emerging 节点 shadowBlur 呼吸动画
  // （~1.6s 周期），让"哪里在变热"一眼可见。⚠️ 不可经
  // setOption({series:[{data:[…]}]}) 做局部更新——data 数组在 merge 模式下是
  // 全量替换语义，只传 emerging 节点会把其余节点全部判删、画布坍缩成几个点
  // （"多次点击后图缩成一个小点"的根因）。改为直接改写节点图形元素的 shadow
  // 属性（零 data diff、不重启力导向）；主 option 重建会生成新元素复位样式，
  // 下一 tick 自动重涂。DAG 视图/无新兴节点时不启动。
  const emergingIdx = useMemo(
    () =>
      data.nodes
        .map((n, i) => (n.type === 'position' && n.status === 'emerging' ? i : -1))
        .filter((i) => i >= 0),
    [data],
  )
  useEffect(() => {
    if (viewMode !== 'graph' || emergingIdx.length === 0) return
    const dark = isDark()
    let phase = 0
    const timer = window.setInterval(() => {
      phase = (phase + 1) % 16
      const glow = 8 + ((Math.sin((phase / 16) * Math.PI * 2) + 1) / 2) * 16
      const chart = chartRef.current
      if (!chart) return
      const list = (chart as unknown as { getModel(): EChartsModel }).getModel().getSeriesByIndex(0)?.getData()
      if (!list) return
      for (const idx of emergingIdx) {
        const el = list.getItemGraphicEl(idx)
        if (el) {
          el.shadowBlur = glow
          el.shadowColor = colorOf(data.nodes[idx], dark)
          el.dirty()
        }
      }
    }, 100)
    return () => window.clearInterval(timer)
  }, [emergingIdx, data, viewMode, themeVersion])

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
    // 延迟一拍触发镜头聚焦：focusNode → applyLodBand → setLodBand 属 effect 内
    // 同步 setState 链（会触发级联重渲染 lint），宏任务化后语义不变（请求仍逐次生效）
    if (!focusRequest) return
    const t = window.setTimeout(() => focusNode(focusRequest.id), 0)
    return () => window.clearTimeout(t)
  }, [focusRequest, focusNode])

  return (
    <div className={`${viewMode === 'graph' ? 'atlas-surface' : ''} relative h-full w-full overflow-hidden ${className ?? ''}`}>
      {viewMode === 'graph' && (
        <div className="pointer-events-none absolute inset-0 z-0 font-mono text-[12px] tracking-[0.18em] text-atlas-muted/75" aria-hidden="true">
          <span className="absolute left-1/2 top-3 -translate-x-1/2">N / 市场</span>
          <span className="absolute right-3 top-1/2 -translate-y-1/2 [writing-mode:vertical-rl]">E / 技术</span>
          <span className="absolute bottom-3 left-1/2 -translate-x-1/2">S / 组织</span>
          <span className="absolute left-3 top-1/2 -translate-y-1/2 [writing-mode:vertical-rl]">W / 业务</span>
          <span className="absolute bottom-3 left-3 text-[8px] text-atlas-muted/70">核心岗位 · 能力与证据</span>
        </div>
      )}
      {/* task T2: 学习路径可用时提供 宏观 DAG / 全局图谱 切换 */}
      {dagEnabled && (
        <div className="absolute left-1/2 top-3 z-30 flex -translate-x-1/2 overflow-hidden rounded-md border border-atlas-grid bg-canvas/90 shadow-sm text-[12px]">
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
        hideSoftSkills={hideSoftSkills}
        onToggleSoftSkills={setHideSoftSkills}
        onReset={resetFilters}
        visibleCount={filterMarks.visibleNodes}
        hiddenCount={data.nodes.length - filterMarks.visibleNodes}
      />
      <div ref={containerRef} className="h-full w-full" />
    </div>
  )
})
