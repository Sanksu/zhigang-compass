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
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import * as echarts from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { GraphData, GraphNode, NodeDetail, NodeType, PositionStatus } from './types'

// 按需注册 — 仅 graph 图表 + tooltip 组件 + canvas 渲染器
// 相比 `import * as echarts from 'echarts'`，可减少约 70% bundle 体积
echarts.use([GraphChart, TooltipComponent, CanvasRenderer])

interface Graph2DProps {
  data: GraphData
  /** 当前选中节点 id（用于高亮） */
  selectedId?: string | null
  onSelectNode: (node: NodeDetail | null) => void
  className?: string
}

/** 节点类型 → 形状 */
const SYMBOL_BY_TYPE: Record<NodeType, string> = {
  position: 'circle',
  skill: 'circle',
  evidence: 'diamond',
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

export function Graph2D({ data, selectedId, onSelectNode, className }: Graph2DProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  // 主题版本号：暗色切换时递增，触发数据 effect 完全重建确保颜色全量刷新
  const [themeVersion, setThemeVersion] = useState(0)

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

    // 节点点击 → 上抛选中节点
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

    // 画布空白点击 → 清空选中
    chart.getZr().on('click', (params) => {
      const target = params.target
      if (!target) {
        onSelectNode(null)
      }
    })

    // 布局完成后再 resize 一次，覆盖初始化时容器为 0 的情况
    requestAnimationFrame(() => {
      chartRef.current?.resize()
    })

    return () => {
      chart.dispose()
      chartRef.current = null
    }
  }, [onSelectNode])

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
      symbol: SYMBOL_BY_TYPE[n.type],
      symbolSize: sizeOf(n),
      category: n.type,
      itemStyle: {
        color: colorOf(n),
        borderColor,
        borderWidth: 1,
      },
      // 不在此处根据 selectedId 设置选中样式 — 选中高亮走 dispatchAction（②）
      label: {
        show: n.type === 'position',
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
        formatter: (params: any) => {
          if (params.dataType !== 'node' || !params.data) return ''
          const d = params.data as GraphNode
          const lines: string[] = [`<b>${d.name}</b>`]
          lines.push(`类型: ${d.type}`)
          if (d.type === 'position' && d.status) lines.push(`状态: ${d.status}`)
          if (d.type === 'skill' && d.level) lines.push(`级别: ${d.level}`)
          if (d.type === 'evidence' && d.source) lines.push(`来源: ${d.source}`)
          if (typeof d.value === 'number') lines.push(`权重: ${d.value}`)
          if (d.description) lines.push(`<span style="color:${mutedColor}">${d.description}</span>`)
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
              shadowColor: (params: any) => colorOf(params.data as GraphNode),
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

    chart.setOption(option, { replaceMerge: ['series'] })
  }, [data, themeVersion])

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

  return <div ref={containerRef} className={className} />
}
