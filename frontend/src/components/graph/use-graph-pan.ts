import { useCallback, useRef } from 'react'
import type { MutableRefObject } from 'react'
import type * as echarts from 'echarts/core'
import type { GraphGroup } from './graph-layout'

interface PanEvent {
  target?: unknown
  offsetX: number
  offsetY: number
}

/**
 * 图谱空白处拖拽平移 hook。
 *
 * ECharts graph 原生 roam 限制起拖点须在节点包围盒内（包围盒外空白拖不动）。
 * 此处对包围盒外的空白按下直接平移 graph 视图 group（与原生 updateViewOnPan 同机制），
 * 包围盒内/节点上不干预（避免双重处理）。
 */
export function useGraphPan(chartRef: MutableRefObject<echarts.ECharts | null>) {
  // 手动平移（空白拖拽）累计像素偏移：聚焦节点换算目标位移时扣除，避免与 roam 平移叠加
  const panOffset = useRef({ x: 0, y: 0 })
  const panning = useRef(false)
  const panLast = useRef({ x: 0, y: 0 })

  // zrender Group（graph 视图的渲染分组），手动平移/聚焦直接改其位置
  const panGroup = useCallback(() => {
    const chart = chartRef.current
    if (!chart) return undefined
    return (chart as unknown as { _chartsViews?: Array<{ group: GraphGroup }> })._chartsViews?.[0]?.group
  }, [chartRef])

  const bindPanEvents = useCallback(
    (chart: echarts.ECharts) => {
      const zr = chart.getZr()

      const onPanDown = (e: PanEvent) => {
        if (e.target) return // 命中节点/边 → 原生节点拖拽
        const group = panGroup()
        if (group) {
          // 起拖点在节点包围盒内 → 原生 roam 已接管平移，此处跳过防双重位移
          const rect = group.getBoundingRect().clone()
          rect.applyTransform(group.transform)
          if (rect.contain(e.offsetX, e.offsetY)) return
        }
        panning.current = true
        panLast.current = { x: e.offsetX, y: e.offsetY }
      }

      const onPanMove = (e: { offsetX: number; offsetY: number }) => {
        if (!panning.current) return
        const dx = e.offsetX - panLast.current.x
        const dy = e.offsetY - panLast.current.y
        if (dx !== 0 || dy !== 0) {
          panLast.current = { x: e.offsetX, y: e.offsetY }
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
        panning.current = false
      }

      zr.on('mousedown', onPanDown)
      zr.on('mousemove', onPanMove)
      zr.on('mouseup', onPanEnd)
      zr.on('globalout', onPanEnd)

      return () => {
        zr.off('mousedown', onPanDown)
        zr.off('mousemove', onPanMove)
        zr.off('mouseup', onPanEnd)
        zr.off('globalout', onPanEnd)
      }
    },
    [panGroup],
  )

  return { panGroup, panOffset, bindPanEvents }
}
