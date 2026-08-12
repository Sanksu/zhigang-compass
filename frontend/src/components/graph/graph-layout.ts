/**
 * 图谱布局相关工具函数与 ECharts 内部最小类型
 *
 * 这些类型/函数都访问 ECharts 私有 API（_chartsViews / getModel / setItemLayout），
 * 集中放在此处，方便统一注释和控制范围。
 */
import type * as echarts from 'echarts/core'

/** zrender Group 最小类型（手动平移 graph 视图用，访问 ECharts 内部 _chartsViews） */
export interface GraphGroup {
  x: number
  y: number
  transform: unknown
  dirty(): void
  getBoundingRect(): {
    clone(): { applyTransform(t: unknown): void; contain(x: number, y: number): boolean }
  }
}

/** ECharts 内部 Model 最小类型（getModel 为私有方法，仅聚焦节点/强制分散计算布局用） */
export interface EChartsModel {
  getSeriesByIndex(index: number): {
    getData(): {
      /** 按名称反查数据项索引（ECharts List.indexOfName，原名 getDataIndexByName 不存在） */
      indexOfName(name: string): number
      getItemLayout(idx: number): number[] | undefined
      /** 写回节点布局坐标（强制分散用） */
      setItemLayout(idx: number, layout: number[]): void
      count(): number
      /** 原始数据项（含自定义字段如 symbolSize） */
      getRawDataItem(idx: number): Record<string, unknown> | undefined
    }
  } | null
}

export interface SpreadOptions {
  /** 节点间最小间隙（px） */
  minGap?: number
  /** 最大迭代轮数 */
  maxIterations?: number
}

/**
 * 布局收敛后强制分散重叠节点。
 *
 * force 布局不感知节点大小，岗位节点尺寸大时即使斥力参数调高仍可能收敛后互相重叠。
 * 检测节点对间距是否小于两者半径之和 + 间隙，沿连线方向推开重叠对，写回 layout 并重绘。
 * 分散不改动节点间相对图结构，仅消除视觉重叠。
 */
export function enforceSpread(chart: echarts.ECharts, options: SpreadOptions = {}): void {
  const { minGap = 14, maxIterations = 3 } = options
  const seriesModel = (chart as unknown as { getModel(): EChartsModel }).getModel().getSeriesByIndex(0)
  const list = seriesModel?.getData()
  if (!list) return

  const count = list.count()
  const positions: (number[] | undefined)[] = []
  const radii: number[] = []
  for (let i = 0; i < count; i++) {
    positions.push(list.getItemLayout(i))
    const raw = list.getRawDataItem(i)
    const size = typeof raw?.symbolSize === 'number' ? raw.symbolSize : 20
    radii.push(size / 2)
  }

  // 重叠节点对：间距 < 半径和 + 间隙 → 沿连线双向推开（每轮迭代收敛）
  let moved = true
  for (let iter = 0; iter < maxIterations && moved; iter++) {
    moved = false
    for (let i = 0; i < count; i++) {
      const pi = positions[i]
      if (!pi) continue
      for (let j = i + 1; j < count; j++) {
        const pj = positions[j]
        if (!pj) continue
        const dx = pj[0] - pi[0]
        const dy = pj[1] - pi[1]
        const dist = Math.sqrt(dx * dx + dy * dy)
        const minDist = radii[i] + radii[j] + minGap
        if (dist > 0 && dist < minDist) {
          // 沿连线方向推开：重叠量均分给两节点
          const push = (minDist - dist) / 2
          const nx = dx / dist
          const ny = dy / dist
          pi[0] -= nx * push
          pi[1] -= ny * push
          pj[0] += nx * push
          pj[1] += ny * push
          moved = true
        }
      }
    }
  }

  // 写回布局坐标并重绘（setItemLayout 直接写 _itemLayouts，无需额外 dirty 标记）
  for (let i = 0; i < count; i++) {
    const p = positions[i]
    if (p) list.setItemLayout(i, [p[0], p[1]])
  }
  chart.getZr().refresh()
}
