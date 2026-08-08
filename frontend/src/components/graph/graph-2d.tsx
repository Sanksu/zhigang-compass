/**
 * ECharts 2D 力导向图组件 — 设计文档 §10.3
 *
 * 实现：
 * - force 力导向布局，节点可拖拽
 * - 节点按类型着色（position 按 status 五状态机，skill 墨色，evidence 灰色菱形）
 * - 边按关系区分（requires 实线 / proves 虚线），按 weight 调整粗细
 * - tooltip 悬停显示节点摘要
 * - 节点点击 → onSelectNode 回调
 * - 暗色模式自动跟随 .dark 类
 *
 * 设计决策（vs 早期版本）：
 * - 数据渲染与选择态分离：数据 effect（deps: [data, themeVersion]）专管画布重建；
 *   选择态 effect（deps: [selectedId]）用 dispatchAction 控制高亮，不重新 setOption，
 *   避免点击节点时 force 布局重算导致闪屏
 * - 主题切换通过递增 themeVersion 触发数据 effect 完全重建，确保节点/边颜色全部刷新
 */
import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from 'react'
import * as echarts from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { GraphData, GraphNode, NodeDetail, NodeType, PositionStatus } from './types'
import { escapeHtml } from '@/lib/utils'

/** ECharts 回调参数最小类型 — 覆盖本组件使用的 tooltip/label/select 回调字段 */
interface EChartsParam {
  dataType?: string
  data?: Record<string, unknown>
  value?: unknown
  name?: string
} 

/** zrender Group 最小类型（手动平移 graph 视图用，访问 ECharts 内部 _chartsViews） */
interface GraphGroup {
  x: number
  y: number
  transform: unknown
  dirty(): void
  getBoundingRect(): {
    clone(): { applyTransform(t: unknown): void; contain(x: number, y: number): boolean }
  }
}

/** ECharts 内部 Model 最小类型（getModel 为私有方法，仅聚焦节点计算布局用） */
interface EChartsModel {
  getSeriesByIndex(index: number): {
    getData(): {
      getDataIndexByName(name: string): number
      getItemLayout(idx: number): number[] | undefined
    }
  } | null
}

// 按需注册 — 仅 graph 图表 + tooltip 组件 + canvas 渲染器
// 相比 `import * as echarts from 'echarts'`，可减少约 70% bundle 体积
echarts.use([GraphChart, TooltipComponent, CanvasRenderer])

interface Graph2DProps {
  data: GraphData
  /** 当前选中节点 id（用于高亮） */
  selectedId?: string | null
  /** 已展开的岗位 id 集合（画布已只含这些岗位的技能，用于样式标记） */
  expandedPositions?: Set<string>
  /** 定位请求：搜索/相似技能点击后聚焦画布上对应节点（含时间戳，重复聚焦同一节点也生效） */
  focusRequest?: { id: string; ts: number } | null
  onSelectNode: (node: NodeDetail | null) => void
  /** 双击岗位 → 展开/收起其技能 */
  onTogglePosition: (id: string) => void
  className?: string
}

/** 父组件可调用的图谱画布方法（聚焦节点 / 重置视角） */
export interface Graph2DHandle {
  focusNode: (id: string) => void
  resetView: () => void
}

/** 节点类型 → 形状 */
const SYMBOL_BY_TYPE: Record<Exclude<NodeType, 'position'>, string> = {
  skill: 'circle',
  evidence: 'diamond',
}

/** 岗位状态机 → 形状（色盲可读：衰退 rect、归档 roundRect 与正常态形状区分） */
const SYMBOL_BY_STATUS: Record<PositionStatus, string> = {
  candidate: 'circle',
  emerging: 'triangle',
  stable: 'circle',
  declining: 'rect',
  archived: 'roundRect',
}

/** 岗位状态机 → 颜色（与 globals.css 中状态色对齐） */
const COLOR_BY_STATUS: Record<PositionStatus, string> = {
  candidate: '#71717a',
  emerging: '#10b981',
  stable: '#3b82f6',
  declining: '#f59e0b',
  archived: '#ef4444',
}

const COLOR_SKILL = '#09090b'
const COLOR_EVIDENCE = '#a1a1aa'

function symbolOf(node: GraphNode): string {
  if (node.type === 'position') return SYMBOL_BY_STATUS[node.status ?? 'candidate']
  return SYMBOL_BY_TYPE[node.type]
}

function colorOf(node: GraphNode): string {
  if (node.type === 'position') return COLOR_BY_STATUS[node.status ?? 'candidate']
  if (node.type === 'skill') return COLOR_SKILL
  return COLOR_EVIDENCE
}

/** value 映射到 symbolSize，范围 [18, 56] */
function sizeOf(node: GraphNode): number {
  const v = node.value ?? 30
  // 岗位 > 技能 > 证据，基础大小不同
  const base = node.type === 'position' ? 36 : node.type === 'skill' ? 24 : 18
  const scaled = base + (v / 100) * 20
  return Math.min(56, Math.max(16, scaled))
}

function weightToWidth(weight?: number): number {
  if (!weight) return 1
  return 0.5 + weight * 2.5 // [0.5, 3]
}

/** 暗色模式判定 — 跟随 documentElement 上的 .dark 类 */
function isDarkMode(): boolean {
  return document.documentElement.classList.contains('dark')
}

export const Graph2D = forwardRef<Graph2DHandle, Graph2DProps>(function Graph2D(
  { data, selectedId, expandedPositions, focusRequest, onSelectNode, onTogglePosition, className },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  // 主题版本号：暗色切换时递增，触发数据 effect 完全重建确保颜色全量刷新
  const [themeVersion, setThemeVersion] = useState(0)
  // 手动平移（空白拖拽）累计像素偏移：聚焦节点换算目标位移时扣除，避免与 roam 平移叠加
  const panOffset = useRef({ x: 0, y: 0 })

  // zrender Group（graph 视图的渲染分组），手动平移/聚焦直接改其位置
  const panGroup = useCallback(() => {
    const chart = chartRef.current
    if (!chart) return undefined
    return (chart as unknown as { _chartsViews?: Array<{ group: GraphGroup }> })._chartsViews?.[0]?.group
  }, [])

  // 初始化 ECharts 实例（仅一次）
  useLayoutEffect(() => {
    if (!containerRef.current) return
    const el = containerRef.current

    // 容器在 useLayoutEffect 时刻可能尺寸为 0（grid 布局未完成计算），
    // 此时 init 会触发 "Can't get DOM width or height" 警告并导致 canvas 不渲染。
    // 处理：init 后立即 resize；若尺寸仍为 0，ResizeObserver 会在布局完成后回调刷新。
    const { width, height } = el.getBoundingClientRect()
    const chart = echarts.init(
      el,
      undefined,
      {
        renderer: 'canvas',
        width: width || undefined,
        height: height || undefined,
      },
    )
    chartRef.current = chart

    // 节点点击 → 上抛选中节点（仅选中，展开/收起走双击或详情面板按钮，避免两种意图耦合）
    chart.on('click', (params) => {
      if (params.dataType === 'node' && params.data) {
        const d = params.data as GraphNode
        onSelectNode({
          id: d.id,
          name: d.name,
          type: d.type,
          status: d.status,
          level: d.level,
          source: d.source,
          value: d.value,
          description: d.description,
        })
      }
    })

    // 节点双击 → 岗位展开/收起其技能
    chart.on('dblclick', (params) => {
      if (params.dataType === 'node' && params.data) {
        const d = params.data as GraphNode
        if (d.type === 'position') onTogglePosition(d.id)
      }
    })

    // 画布空白点击 → 清空选中
    chart.getZr().on('click', (params) => {
      const target = params.target
      if (!target) {
        onSelectNode(null)
      }
    })

    // 外围空白拖拽平移：ECharts graph 原生 roam 限制起拖点须在节点包围盒内
    // （包围盒外空白拖不动）。此处对包围盒外的空白按下直接平移 graph 视图
    // group（与原生 updateViewOnPan 同机制），包围盒内/节点上不干预（避免双重处理）。
    // graphRoam dispatchAction 不产生视觉平移（仅更新 View 状态），故直接操作 group。
    const zr = chart.getZr()
    let panning = false
    let panLastX = 0
    let panLastY = 0
    const onPanDown = (e: { target?: unknown; offsetX: number; offsetY: number }) => {
      if (e.target) return // 命中节点/边 → 原生节点拖拽
      const group = panGroup()
      if (group) {
        // 起拖点在节点包围盒内 → 原生 roam 已接管平移，此处跳过防双重位移
        const rect = group.getBoundingRect().clone()
        rect.applyTransform(group.transform)
        if (rect.contain(e.offsetX, e.offsetY)) return
      }
      panning = true
      panLastX = e.offsetX
      panLastY = e.offsetY
    }
    const onPanMove = (e: { offsetX: number; offsetY: number }) => {
      if (!panning) return
      const dx = e.offsetX - panLastX
      const dy = e.offsetY - panLastY
      if (dx !== 0 || dy !== 0) {
        panLastX = e.offsetX
        panLastY = e.offsetY
        const group = panGroup()
        if (group) {
          panOffset.current.x += dx
          panOffset.current.y += dy
          group.x += dx
          group.y += dy
          group.dirty()
          chart.getZr().refresh()
        }
      }
    }
    const onPanEnd = () => {
      panning = false
    }
    zr.on('mousedown', onPanDown)
    zr.on('mousemove', onPanMove)
    zr.on('mouseup', onPanEnd)
    zr.on('globalout', onPanEnd)

    // 布局完成后再 resize 一次，覆盖初始化时容器为 0 的情况
    requestAnimationFrame(() => {
      chartRef.current?.resize()
    })

    return () => {
      chart.dispose()
      chartRef.current = null
    }
    // panGroup 为 useCallback 稳定引用，列入依赖仅满足 exhaustive-deps，不影响仅执行一次
  }, [onSelectNode, onTogglePosition, panGroup])

  // 容器尺寸变化 → resize
  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver(() => {
      chartRef.current?.resize()
    })
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  // 暗色模式变化 → 递增主题版本号触发数据 effect 全量重建
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

  // ============================================================
  // ① 数据渲染 effect — 仅 data 或 themeVersion 变化时重建
  //    不依赖 selectedId，避免点击节点触发 force 布局重算
  // ============================================================
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    const dark = isDarkMode()
    const textColor = dark ? '#fafafa' : '#09090b'
    const mutedColor = dark ? '#a1a1aa' : '#71717a'
    const borderColor = dark ? '#27272a' : '#e4e4e7'

    const nodes = data.nodes.map((n) => ({
      // 原始字段透传（含 id/name），供 ECharts 与 tooltip/click 回调使用
      ...n,
      symbol: symbolOf(n),
      symbolSize: sizeOf(n),
      category: n.type,
      itemStyle: {
        color: colorOf(n),
        borderColor,
        // 展开的岗位加粗描边，提示其技能当前可见（可点击收起）
        borderWidth: expandedPositions?.has(n.id) ? 3 : 1,
      },
      // 不在此处根据 selectedId 设置选中样式 — 选中高亮走 dispatchAction（②）
      label: {
        // 岗位恒显；展开揭示的技能节点显示名字（未展开时画布无技能节点）
        show: n.type !== 'evidence',
        position: 'right',
        color: textColor,
        fontSize: 11,
        formatter: n.type === 'position' ? `{a|${n.name}}` : n.name,
        rich: {
          a: { fontWeight: 600, fontSize: 12 },
        },
      },
    }))

    const links = data.edges.map((e) => ({
      source: e.source,
      target: e.target,
      lineStyle: {
        width: weightToWidth(e.weight),
        color: e.relation === 'proves' ? mutedColor : borderColor,
        opacity: e.relation === 'proves' ? 0.5 : 0.7,
        type: e.relation === 'proves' ? 'dashed' : 'solid',
        curveness: 0.15,
      },
    }))

    const option: echarts.EChartsCoreOption = {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item',
        backgroundColor: dark ? '#18181b' : '#ffffff',
        borderColor: dark ? '#3f3f46' : '#d4d4d8',
        borderWidth: 1,
        textStyle: { color: textColor, fontSize: 12 },
        formatter: (params: EChartsParam) => {
          if (params.dataType !== 'node' || !params.data) return ''
          const d = params.data as unknown as GraphNode
          // tooltip 经 innerHTML 渲染，外部可控的 name/description 等必须先转义（防 XSS）
          const lines: string[] = [`<b>${escapeHtml(d.name)}</b>`]
          lines.push(`类型: ${escapeHtml(d.type)}`)
          if (d.type === 'position' && d.status) lines.push(`状态: ${escapeHtml(d.status)}`)
          if (d.type === 'skill' && d.level) lines.push(`级别: ${escapeHtml(d.level)}`)
          if (d.type === 'evidence' && d.source) lines.push(`来源: ${escapeHtml(d.source)}`)
          if (typeof d.value === 'number') lines.push(`权重: ${d.value}`)
          if (d.description) lines.push(`<span style="color:${mutedColor}">${escapeHtml(d.description)}</span>`)
          return lines.join('<br/>')
        },
      },
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          // 首次渲染启用力导向入场动画；数据变化（视图切换）时 layoutAnimation: true
          // 让用户感知新布局的渐进收敛，比突变更自然
          force: {
            repulsion: 180,
            edgeLength: [60, 180],
            gravity: 0.08,
            friction: 0.6,
            layoutAnimation: true,
          },
          scaleLimit: { min: 0.3, max: 4 },
          emphasis: {
            focus: 'adjacency',
            lineStyle: { width: 3, opacity: 0.9 },
            label: { show: true },
          },
          selectedMode: 'single',
          select: {
            itemStyle: {
              borderColor: textColor,
              borderWidth: 3,
              shadowBlur: 12,
              shadowColor: (params: EChartsParam) => colorOf(params.data as unknown as GraphNode),
            },
            label: {
              show: true,
              color: textColor,
              fontSize: 11,
            },
          },
          categories: [
            { name: 'position' },
            { name: 'skill' },
            { name: 'evidence' },
          ],
          data: nodes,
          links,
        },
      ],
    }

    // 默认 merge（不 replaceMerge）：ECharts 按 name diff 保留已有节点坐标，
    // 展开/收起时仅新增/移除技能节点，已有节点位置不被重置
    chart.setOption(option)
  }, [data, themeVersion, expandedPositions])

  // ============================================================
  // ② 选中态高亮 effect — 仅 selectedId 变化时触发
  //    用 dispatchAction 控制 ECharts 原生选中，不 setOption 避免 force 布局重算
  // ============================================================
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    if (selectedId) {
      chart.dispatchAction({ type: 'select', id: selectedId })
    } else {
      chart.dispatchAction({ type: 'unselect' })
    }
  }, [selectedId])

  // ============================================================
  // ③ 聚焦节点 / 重置视角（父组件经 ref 调用）
  //    聚焦：把节点移动到画布中心并短暂高亮；重置：还原初始视角
  // ============================================================
  const focusNode = useCallback(
    (id: string) => {
      const chart = chartRef.current
      if (!chart) return
      // 按当前可见数据中的 name 反查 ECharts 数据项（data item 的 id 为节点 id）
      const node = data.nodes.find((n) => n.id === id)
      if (!node) return
      // getModel 为 ECharts 私有方法，此处按最小模型断言访问（与 panGroup 访问 _chartsViews 同理）
      const seriesModel = (chart as unknown as { getModel(): EChartsModel }).getModel().getSeriesByIndex(0)
      const list = seriesModel?.getData()
      if (!list) return
      const idx = list.getDataIndexByName(node.name)
      if (idx < 0) return
      const layout = list.getItemLayout(idx)
      if (!layout || layout.length < 2) return
      // 节点当前画布像素位置（含 ECharts roam 缩放/平移；手动 group 偏移单独追踪）
      const pixel = chart.convertToPixel({ seriesIndex: 0 }, [layout[0], layout[1]])
      if (!pixel || pixel.length < 2) return
      const dx = chart.getWidth() / 2 - (pixel[0] + panOffset.current.x)
      const dy = chart.getHeight() / 2 - (pixel[1] + panOffset.current.y)
      const group = panGroup()
      if (group) {
        group.x += dx
        group.y += dy
        group.dirty()
      }
      panOffset.current.x += dx
      panOffset.current.y += dy
      chart.getZr().refresh()
      // 高亮目标节点约 1.5s，提示用户已定位
      chart.dispatchAction({ type: 'highlight', seriesIndex: 0, name: node.name })
      window.setTimeout(() => {
        chart.dispatchAction({ type: 'downplay', seriesIndex: 0, name: node.name })
      }, 1500)
    },
    [data, panGroup],
  )

  const resetView = useCallback(() => {
    const chart = chartRef.current
    if (!chart) return
    // 重置 ECharts roam 缩放/平移回初始视角
    chart.dispatchAction({ type: 'restore' })
    // 清掉手动平移累积的 group 偏移
    const group = panGroup()
    if (group) {
      group.x -= panOffset.current.x
      group.y -= panOffset.current.y
      group.dirty()
    }
    panOffset.current = { x: 0, y: 0 }
    chart.getZr().refresh()
  }, [panGroup])

  useImperativeHandle(ref, () => ({ focusNode, resetView }), [focusNode, resetView])

  // 定位请求 → 聚焦画布（依赖 data：展开岗位后节点才入画布，数据到位后再聚焦）
  useEffect(() => {
    if (focusRequest) focusNode(focusRequest.id)
  }, [focusRequest, data, focusNode])

  return <div ref={containerRef} className={className} />
})
