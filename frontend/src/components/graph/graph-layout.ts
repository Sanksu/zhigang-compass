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
  /** getModel 在 chart dispose 后返回 null（残留 rAF 回调帧访问时，调用方需空值保护） */
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
  /** 平滑推开：true 时从当前位置 300ms 插值动画到目标（smoothstep），
   *   false（默认）直接写回——硬位移会让节点瞬间跳变（动画中抖动 / 静止时突兀） */
  animate?: boolean
  /** 平滑动画时长（ms，animate=true 时生效） */
  duration?: number
}

/** 岗位节点是否存在重叠对（间距 < 半径和 + minGap - 1px 容差）。
 *  供图谱轮询兜底逐帧判定：friction 大时布局提前冻结、展开不足，岗位可能重叠；
 *  容差 1px 防浮点边界死循环——enforceSpread 推开到 minDist 后浮点误差可能使
 *  间距略小于 minDist，严格 `<` 判定会永远触发（实测 min 99.0 → 99.0 死循环）。 */
export function hasPositionOverlap(chart: echarts.ECharts, minGap = 60): boolean {
  const seriesModel = (chart as unknown as { getModel(): EChartsModel | null }).getModel()?.getSeriesByIndex(0)
  const list = seriesModel?.getData()
  if (!list) return false

  const count = list.count()
  const pos: number[][] = []
  const radii: number[] = []
  for (let i = 0; i < count; i++) {
    const raw = list.getRawDataItem(i)
    if (raw?.type !== 'position') continue
    const l = list.getItemLayout(i)
    if (!l || l.length < 2) continue
    pos.push([l[0], l[1]])
    const size = typeof raw?.symbolSize === 'number' ? raw.symbolSize : 20
    radii.push(size / 2)
  }
  for (let i = 0; i < pos.length; i++) {
    for (let j = i + 1; j < pos.length; j++) {
      const d = Math.hypot(pos[i][0] - pos[j][0], pos[i][1] - pos[j][1])
      if (d < radii[i] + radii[j] + minGap - 1) return true
    }
  }
  return false
}

/**
 * 布局收敛后强制分散重叠节点。
 *
 * force 布局不感知节点大小，岗位节点尺寸大时即使斥力参数调高仍可能收敛后互相重叠。
 * 检测节点对间距是否小于两者半径之和 + 间隙，沿连线方向推开重叠对，写回 layout 并重绘。
 * 分散不改动节点间相对图结构，仅消除视觉重叠。
 *
 * animate=true 时位移以 300ms 平滑插值（smoothstep）执行——硬写回会让节点瞬间跳变，
 * 在布局动画中表现为抖动、在静止时表现为突兀跳开（2026-08-15 修复）。
 */
export function enforceSpread(chart: echarts.ECharts, options: SpreadOptions = {}): void {
  const { minGap = 14, maxIterations = 3, animate = false, duration = 300 } = options
  const seriesModel = (chart as unknown as { getModel(): EChartsModel | null }).getModel()?.getSeriesByIndex(0)
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
  // 目标位置副本（迭代只在副本上推进，from 保留插值起点）
  const target: (number[] | undefined)[] = positions.map((p) => (p ? [p[0], p[1]] : undefined))

  // 重叠节点对：间距 < 半径和 + 间隙 → 沿连线双向推开（每轮迭代收敛）
  let moved = true
  for (let iter = 0; iter < maxIterations && moved; iter++) {
    moved = false
    for (let i = 0; i < count; i++) {
      const pi = target[i]
      if (!pi) continue
      for (let j = i + 1; j < count; j++) {
        const pj = target[j]
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

  if (!animate) {
    // 硬写回（原行为）
    for (let i = 0; i < count; i++) {
      const p = target[i]
      if (p) list.setItemLayout(i, [p[0], p[1]])
    }
    chart.getZr().refresh()
    return
  }

  // 平滑插值：from（当前）→ target（目标），smoothstep 缓动，避免硬跳
  const from: (number[] | undefined)[] = positions.map((p) => (p ? [p[0], p[1]] : undefined))
  const start = performance.now()
  const tick = () => {
    const t = Math.min(1, (performance.now() - start) / duration)
    const e = t * t * (3 - 2 * t) // smoothstep
    for (let i = 0; i < count; i++) {
      const f = from[i]
      const to = target[i]
      if (f && to) {
        list.setItemLayout(i, [f[0] + (to[0] - f[0]) * e, f[1] + (to[1] - f[1]) * e])
      }
    }
    try {
      chart.getZr().refresh()
    } catch {
      // chart 已 dispose（视图切换/组件卸载后残留 rAF 帧），放弃本次动画
      return
    }
    if (t < 1) requestAnimationFrame(tick)
  }
  tick()
}
