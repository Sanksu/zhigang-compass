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
import { skillLabelThreshold } from './graph-utils'
import { enforceSpread, type EChartsModel } from './graph-layout'
import { useGraphPan } from './use-graph-pan'
import { escapeHtml } from '@/lib/utils'

/** ECharts 回调参数最小类型 — 覆盖本组件使用的 tooltip/label/select 回调字段 */
interface EChartsParam {
  dataType?: string
  data?: Record<string, unknown>
  value?: unknown
  name?: string
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

const COLOR_SKILL_LIGHT = '#09090b'
const COLOR_SKILL_DARK = '#fafafa'
const COLOR_EVIDENCE = '#a1a1aa'

function symbolOf(node: GraphNode): string {
  if (node.type === 'position') return SYMBOL_BY_STATUS[node.status ?? 'candidate']
  return SYMBOL_BY_TYPE[node.type]
}

/** 技能节点颜色跟随主题：暗色下用浅色，避免技能节点与深色背景融为一体 */
function colorOf(node: GraphNode, dark: boolean): string {
  if (node.type === 'position') return COLOR_BY_STATUS[node.status ?? 'candidate']
  if (node.type === 'skill') return dark ? COLOR_SKILL_DARK : COLOR_SKILL_LIGHT
  return COLOR_EVIDENCE
}

/** value 映射到 symbolSize，范围 [16, 56]；技能/证据节点整体缩小，减少与岗位节点的视觉干扰 */
function sizeOf(node: GraphNode): number {
  const v = node.value ?? 30
  // 岗位 > 技能 > 证据，基础大小不同
  const base = node.type === 'position' ? 36 : node.type === 'skill' ? 20 : 15
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
  // 空白拖拽平移 hook：提供 group 访问、累计偏移、事件绑定
  const { panGroup, panOffset, bindPanEvents } = useGraphPan(chartRef)

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
        const d = params.data as GraphNode & { displayValue?: number }
        onSelectNode({
          id: d.id,
          name: d.name,
          type: d.type,
          status: d.status,
          // 布局质量已把岗位 value 放大 3 倍，展示侧还原为原始 value（displayValue 兜底）
          value: d.displayValue ?? d.value,
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

    // 外围空白拖拽平移（实现见 useGraphPan hook）
    const unbindPan = bindPanEvents(chart)

    // 布局收敛 → 强制分散重叠的岗位节点。
    // 只用 forceLayoutEnd：finished 会在渲染动画期间多次触发，导致展开时强制分散被反复
    // 执行、节点抖动；forceLayoutEnd 在力导向算法收敛后只触发一次，此时再推开重叠对。
    const onForceLayoutEnd = () => {
      enforceSpread(chart, { minGap: 32, maxIterations: 5 })
    }
    chart.on('forceLayoutEnd', onForceLayoutEnd)

    // 布局完成后再 resize 一次，覆盖初始化时容器为 0 的情况
    requestAnimationFrame(() => {
      chartRef.current?.resize()
    })

    return () => {
      unbindPan()
      chart.off('forceLayoutEnd', onForceLayoutEnd)
      chart.dispose()
      chartRef.current = null
    }
    // bindPanEvents 为 useCallback 稳定引用；enforceSpread 为顶层工具函数
  }, [onSelectNode, onTogglePosition, bindPanEvents])

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
    // 技能标签密度阈值（低于中位数不常显，悬停时经 emphasis 显示）
    const labelThreshold = skillLabelThreshold(data.nodes)

    const nodes = data.nodes.map((n) => ({
      // 原始字段透传（含 id/name），供 ECharts 与 tooltip/click 回调使用
      ...n,
      // 力导向布局质量（value 参与斥力计算，越大排斥越强）：岗位 ×3 放大布局权重，
      // 让高频大岗位主动排斥远离，避免斥力不足时互相重叠（2026-08-11）。
      // 注意此 value 会覆盖透传的原始 value，tooltip/label 显示改用 displayValue 兜底。
      value: n.type === 'position' ? (n.value ?? 0) * 3 : (n.value ?? 0),
      displayValue: n.value,
      symbol: symbolOf(n),
      symbolSize: sizeOf(n),
      category: n.type,
      itemStyle: {
        color: colorOf(n, dark),
        borderColor,
        // 展开的岗位加粗描边，提示其技能当前可见（可点击收起）
        borderWidth: expandedPositions?.has(n.id) ? 3 : 1,
      },
      // 不在此处根据 selectedId 设置选中样式 — 选中高亮走 dispatchAction（②）
      label: {
        // 岗位恒显；技能节点仅高关联度常显（低关联悬停/选中时经 emphasis 显示）；
        // evidence 节点无标签
        show:
          n.type === 'position' ||
          (n.type === 'skill' && (n.value ?? 0) >= labelThreshold),
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
        color: borderColor,
        opacity: 0.7,
        // 后端仅返回 REQUIRES 边（契约 GraphEdge 无 relation），一律实线
        type: 'solid',
        // 二部图单重边，无需弧线区分 → 直线更清晰
        curveness: 0,
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
          const d = params.data as unknown as GraphNode & { displayValue?: number }
          // tooltip 经 innerHTML 渲染，外部可控的 name 等必须先转义（防 XSS）
          const lines: string[] = [`<b>${escapeHtml(d.name)}</b>`]
          lines.push(`类型: ${escapeHtml(d.type)}`)
          if (d.type === 'position' && d.status) lines.push(`状态: ${escapeHtml(d.status)}`)
          // 权重显示原始 value（布局质量放大值不展示给用户）
          const displayValue = d.displayValue ?? d.value
          if (typeof displayValue === 'number') lines.push(`权重: ${displayValue}`)
          // 操作提示：降低新用户学习成本
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
          // 力导向布局动画开关：必须保持 true（异步逐帧），false 会让 ECharts 在
          // setOption 时同步递归跑完 ~511 步布局（friction 0.6 每步 ×0.992 到 0.01），
          // 主线程长时间阻塞 → 页面冻结（2026-08-08 实测 techStack 视图 10.8s）。
          // 动画时长由收敛步数决定（固定约 8s），节点数量影响每步成本。
          force: {
            // 斥力/边长/重力调参（2026-08-11）：
            // - repulsion 必须是数组 [low, high]：ECharts 用 linearMap(value, extent, [low,high])
            //   按节点 value 线性映射斥力（固定值 350 对所有节点常数，岗位 value×3 布局放大无效）。
            //   岗位 value×3 后分布在高端 → 斥力接近 high，技能在低端 → 接近 low。
            // - gravity 调小，减弱向中心聚拢，避免高频大岗位堆在中央重叠。
            repulsion: [150, 600],
            edgeLength: [80, 240],
            gravity: 0.04,
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
              shadowColor: (params: EChartsParam) => colorOf(params.data as unknown as GraphNode, dark),
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
      const idx = list.indexOfName(node.name)
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
    [data, panGroup, panOffset],
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
  }, [panGroup, panOffset])

  useImperativeHandle(ref, () => ({ focusNode, resetView }), [focusNode, resetView])

  // 定位请求 → 聚焦画布（依赖 data：展开岗位后节点才入画布，数据到位后再聚焦）
  useEffect(() => {
    if (focusRequest) focusNode(focusRequest.id)
  }, [focusRequest, data, focusNode])

  return <div ref={containerRef} className={className} />
})
