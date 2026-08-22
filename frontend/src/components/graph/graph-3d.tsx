/**
 * 3D 力导向图组件 — 设计文档 §10.3 3D 可选模式
 *
 * 基于 react-force-graph-3d（Three.js WebGL 渲染）。
 *
 * 设计决策：
 * - 节点配色与 2D 完全一致（position 五状态机 / skill 浅色或墨色 / evidence 灰色），暗色自动跟随
 * - 节点常显文字标签（nodeThreeObject 自绘 Sprite）：3D 无 hover 空间定位，全量标签保证可读性；
 *   选中节点放大 + 白色光环高亮
 * - 双击岗位节点 → 展开/收起其技能（与 2D 交互一致）
 * - 容器尺寸由 ResizeObserver 自动追踪
 * - A1 修复：节点 Three.js 对象按 stateKey 缓存，避免每帧重建 Canvas/Texture（Sprite 泄漏），
 *   状态变化时 disposeObject3D 释放 GPU 资源
 * - A2 修复：节点数 > 200 时降低 d3 力模拟参数，保障 30fps（设计文档 §6.3）
 * - B4 新增：onNodeHover Tooltip，鼠标悬停节点显示即时反馈
 */
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import { isDark } from '@/lib/utils'
import ForceGraph3D, { type ForceGraphMethods, type NodeObject } from 'react-force-graph-3d'
import * as THREE from 'three'
import type { GraphData, GraphNode, NodeDetail } from './types'
import { COLOR_BY_STATUS, COLOR_SOFT_DARK, COLOR_SOFT_LIGHT, isSoftSkill, nodeRadius } from './graph-utils'

/** react-force-graph-3d 类型定义未暴露的 d3 力模拟参数方法（A2 帧率保障用）。 */
type ForceGraphD3Params = ForceGraphMethods<NodeObject<GraphNode>> & {
  d3AlphaDecay?: (alpha: number) => unknown
  d3VelocityDecay?: (v: number) => unknown
}

interface Graph3DProps {
  data: GraphData
  /** 已展开的岗位 id 集合（画布已只含这些岗位的技能，用于样式标记） */
  expandedPositions?: Set<string>
  /** 当前选中节点 id（放大 + 光环高亮） */
  selectedId?: string | null
  /** 定位请求：搜索/相似技能点击后把相机聚焦到对应节点（含时间戳，重复聚焦同一节点也生效） */
  focusRequest?: { id: string; ts: number } | null
  onSelectNode: (node: NodeDetail | null) => void
  /** 双击岗位 → 展开/收起其技能（与 2D 交互一致） */
  onTogglePosition?: (id: string) => void
  /** 域超节点双击展开/收起（panorama 聚合下钻，与 Graph2D 对齐） */
  onToggleDomain?: (id: string) => void
  className?: string
}

/** 父组件可调用的 3D 画布方法（聚焦节点 / 重置视角 / 演示书签飞行） */
export interface Graph3DHandle {
  focusNode: (id: string) => void
  resetView: () => void
  /** 演示书签：镜头平滑飞行到指定节点（dist 为取景距离，缺省 28；布局未静止时自动重试） */
  flyTo: (id: string, dist?: number) => void
}

const COLOR_EVIDENCE = '#a1a1aa'

function skillColor(dark: boolean): string {
  return dark ? '#fafafa' : '#09090b'
}

function nodeColor(node: GraphNode, dark: boolean): string {
  if (node.type === 'position') return COLOR_BY_STATUS[node.status ?? 'candidate']
  if (isSoftSkill(node)) return dark ? COLOR_SOFT_DARK : COLOR_SOFT_LIGHT
  if (node.type === 'skill') return skillColor(dark)
  return COLOR_EVIDENCE
}

/** 节点布局质量（影响力导向布局，与视觉尺寸无关——视觉尺寸由 buildNodeObject 控制） */
function nodeVal(node: GraphNode): number {
  const v = node.value ?? 30
  const base = node.type === 'position' ? 8 : node.type === 'skill' ? 5 : 3
  return base + (v / 100) * 8
}

/** 常显文字标签 Sprite：半透明圆角底 + 主题色文字，任意背景可读 */
function makeTextSprite(text: string, dark: boolean, fontSize = 14): THREE.Sprite {
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d')
  if (!ctx) return new THREE.Sprite()
  const font = `600 ${fontSize}px Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif`
  ctx.font = font
  const textW = ctx.measureText(text).width
  const pad = 7
  const w = Math.ceil(textW + pad * 2)
  const h = fontSize + 10
  canvas.width = w
  canvas.height = h
  ctx.beginPath()
  ctx.roundRect(0, 0, w, h, 5)
  ctx.fillStyle = dark ? 'rgba(9,9,11,0.72)' : 'rgba(255,255,255,0.82)'
  ctx.fill()
  ctx.fillStyle = dark ? '#fafafa' : '#09090b'
  ctx.font = font
  ctx.textBaseline = 'middle'
  ctx.fillText(text, pad, h / 2)
  const texture = new THREE.CanvasTexture(canvas)
  texture.needsUpdate = true
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false }),
  )
  // 按 canvas 像素比例缩放（texture 高 w/h，保持 2D 字号观感）
  sprite.scale.set(w / 24, h / 24, 1)
  return sprite
}

/** 单节点三维对象：球体 + （选中光环）+ 常显文字标签 */
function buildNodeObject(
  node: GraphNode,
  dark: boolean,
  selected: boolean,
  expanded: boolean,
): THREE.Object3D {
  const r = nodeRadius(node, selected, expanded)
  const group = new THREE.Group()
  const sphere = new THREE.Mesh(
    new THREE.SphereGeometry(r, 20, 20),
    new THREE.MeshBasicMaterial({
      color: nodeColor(node, dark),
      transparent: true,
      opacity: selected ? 1 : 0.95,
    }),
  )
  group.add(sphere)

  // 选中光环：贴合球面的扁平圆环
  if (selected) {
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(r * 1.15, r * 1.5, 32),
      new THREE.MeshBasicMaterial({
        color: '#ffffff',
        transparent: true,
        opacity: 0.65,
        side: THREE.DoubleSide,
      }),
    )
    ring.rotation.x = Math.PI / 2
    group.add(ring)
  }

  // 岗位标签字号更大（主节点），技能/证据小字号
  const fontSize = node.type === 'position' ? 14 : 11
  const label = makeTextSprite(node.name, dark, fontSize)
  label.position.set(0, r + (fontSize + 6) / 24, 0)
  group.add(label)
  return group
}

/**
 * A1：递归释放 Object3D 持有的 GPU 资源（Geometry / Material / Texture）。
 * buildNodeObject 中每个球体/圆环/Sprite 都持有 BufferGeometry + Material + CanvasTexture，
 * 若不显式 dispose，WebGL 侧显存持续增长（JS GC 无法感知 GPU 资源）。
 */
function disposeObject3D(obj: THREE.Object3D): void {
  obj.traverse((child) => {
    if (child instanceof THREE.Mesh) {
      child.geometry?.dispose()
      if (Array.isArray(child.material)) {
        child.material.forEach((m) => m.dispose())
      } else {
        ;(child.material as THREE.Material | undefined)?.dispose()
      }
    }
    if (child instanceof THREE.Sprite) {
      const mat = child.material as THREE.SpriteMaterial
      mat.map?.dispose()
      mat.dispose()
    }
  })
}

export const Graph3D = forwardRef<Graph3DHandle, Graph3DProps>(function Graph3D(
  { data, expandedPositions, selectedId, focusRequest, onSelectNode, onTogglePosition, onToggleDomain, className },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [dimensions, setDimensions] = useState({ width: 800, height: 640 })
  const [dark, setDark] = useState(isDark)
  // react-force-graph-3d 实例句柄（cameraPosition / zoomToFit 控制相机）。
  // 显式指定 NodeType=GraphNode，保证组件回调参数（onNodeClick 等）与本地类型兼容
  const fgRef = useRef<ForceGraphD3Params | undefined>(undefined)

  // A1：节点对象缓存（Map<stateKey, Object3D>）
  // stateKey = "nodeId:dark:isSelected:isExpanded"，相同 stateKey 命中缓存不重建 Canvas
  const nodeObjectCacheRef = useRef(new Map<string, THREE.Object3D>())
  // 按 nodeId 记录当前 stateKey，用于状态变化时定位旧对象并 dispose
  const nodeStateKeyRef = useRef(new Map<string, string>())

  // B4：Hover Tooltip 状态
  const [hoverInfo, setHoverInfo] = useState<{ node: GraphNode; x: number; y: number } | null>(null)
  // 鼠标在容器内的相对位置（mousemove 实时更新，供 onNodeHover 读取）
  const mousePosRef = useRef({ x: 0, y: 0 })

  // 容器尺寸追踪
  useEffect(() => {
    if (!containerRef.current) return
    const el = containerRef.current
    const ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect
      if (width > 0 && height > 0) {
        setDimensions({ width: Math.floor(width), height: Math.floor(height) })
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // 暗色模式跟随
  useEffect(() => {
    const el = document.documentElement
    const observer = new MutationObserver(() => setDark(isDark()))
    observer.observe(el, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])

  // B4：鼠标位置跟踪（绑定容器，避免全局事件开销）
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const onMove = (e: MouseEvent) => {
      const rect = el.getBoundingClientRect()
      mousePosRef.current = { x: e.clientX - rect.left, y: e.clientY - rect.top }
    }
    el.addEventListener('mousemove', onMove)
    return () => el.removeEventListener('mousemove', onMove)
  }, [])

  // A1：数据（节点集合）变化时清空缓存，释放旧 GPU 资源
  useEffect(() => {
    const cache = nodeObjectCacheRef.current
    cache.forEach((obj) => disposeObject3D(obj))
    cache.clear()
    nodeStateKeyRef.current.clear()
  }, [data])

  // A2：节点数 > 200 时降低 d3 力模拟参数，保障 30fps（设计文档 §6.3）
  useEffect(() => {
    const fg = fgRef.current
    if (!fg) return
    const nodeCount = data.nodes.length
    if (nodeCount > 200) {
      // 弱化斥力 + 加速收敛：牺牲布局精度换帧率稳定
      fg.d3Force('charge')?.strength(-30)
      fg.d3AlphaDecay?.(0.04)
      fg.d3VelocityDecay?.(0.5)
    } else {
      // 正常参数：布局质量优先
      fg.d3Force('charge')?.strength(-120)
      fg.d3AlphaDecay?.(0.0228)
      fg.d3VelocityDecay?.(0.4)
    }
  }, [data.nodes.length])

  // 组件卸载时释放所有缓存的 Three.js 对象，防止显存泄漏
  useEffect(() => {
    const cache = nodeObjectCacheRef.current
    return () => {
      cache.forEach((obj) => disposeObject3D(obj))
      cache.clear()
      nodeStateKeyRef.current.clear()
    }
  }, [])

  // 适配 ForceGraph3D 的数据格式（useMemo 避免引用变化触发布局重算）
  const fgData = useMemo(
    () => ({
      nodes: data.nodes.map((n) => ({ ...n })),
      links: data.edges.map((e) => ({ ...e })),
    }),
    [data],
  )

  const bgColor = dark ? '#09090b' : '#ffffff'
  const linkColor = dark ? '#52525b' : '#a1a1aa'

  // 单击 → 选中；双击岗位 → 展开/收起其技能（ForceGraph3D 无原生双击，用点击间隔检测）
  const lastClickRef = useRef<{ id: string; ts: number }>({ id: '', ts: 0 })
  const handleNodeClick = useCallback(
    (node: GraphNode) => {
      const now = Date.now()
      if (node.id === lastClickRef.current.id && now - lastClickRef.current.ts < 300) {
        // 双击：域超节点展开/收起域内岗位；普通岗位展开/收起技能；非两者不处理
        lastClickRef.current = { id: '', ts: 0 }
        if (node.isDomain) onToggleDomain?.(node.id)
        else if (node.type === 'position') onTogglePosition?.(node.id)
        return
      }
      lastClickRef.current = { id: node.id, ts: now }
      onSelectNode({
        id: node.id,
        name: node.name,
        type: node.type,
        status: node.status,
        value: node.value,
        isDomain: node.isDomain,
        memberCount: node.memberCount,
      })
    },
    [onSelectNode, onTogglePosition, onToggleDomain],
  )

  // 点击空白区域清除选中
  const handleBackgroundClick = useCallback(() => {
    onSelectNode(null)
  }, [onSelectNode])

  // B4：Hover 节点 → 更新 Tooltip 位置和内容
  const handleNodeHover = useCallback((node: NodeObject<GraphNode> | null) => {
    if (!node) {
      setHoverInfo(null)
      return
    }
    setHoverInfo({
      node: node as GraphNode,
      x: mousePosRef.current.x,
      y: mousePosRef.current.y,
    })
  }, [])

  // A1：节点对象缓存 getter（核心修复）
  // stateKey 编码所有影响外观的维度，命中时直接返回缓存对象，不重建 Canvas/Texture
  const getNodeObject = useCallback(
    (rawNode: unknown) => {
      const node = rawNode as GraphNode
      const isSelected = selectedId === node.id
      const isExpanded = expandedPositions?.has(node.id) ?? false
      const stateKey = `${node.id}:${dark}:${isSelected}:${isExpanded}`

      const prevKey = nodeStateKeyRef.current.get(node.id)
      if (prevKey && prevKey !== stateKey) {
        // 状态变化：dispose 旧对象的 GPU 资源，从缓存中移除
        const oldObj = nodeObjectCacheRef.current.get(prevKey)
        if (oldObj) {
          disposeObject3D(oldObj)
          nodeObjectCacheRef.current.delete(prevKey)
        }
      }

      if (!nodeObjectCacheRef.current.has(stateKey)) {
        const obj = buildNodeObject(node, dark, isSelected, isExpanded)
        nodeObjectCacheRef.current.set(stateKey, obj)
        nodeStateKeyRef.current.set(node.id, stateKey)
      }

      return nodeObjectCacheRef.current.get(stateKey)!
    },
    [dark, selectedId, expandedPositions],
  )

  // 镜头飞行到节点：相机移动到节点斜上方，看向节点（node 位置为 force 引擎在
  // fgData 节点上写入的实时坐标）。力导向未静止时坐标可能取不到，短间隔重试
  // 至多 3 次；focusNode（搜索定位）与演示书签 flyTo 共用本实现。
  const flyTo = useCallback(
    (id: string, dist = 28) => {
      let attempts = 0
      const tryFly = () => {
        const fg = fgRef.current
        if (!fg) return
        const node = fgData.nodes.find((n) => n.id === id) as NodeObject<GraphNode> | undefined
        if (node && typeof node.x === 'number' && typeof node.y === 'number' && typeof node.z === 'number') {
          // 取景距离：节点视觉半径最大约 10，28 单位距离可完整框住节点 + 文字标签
          fg.cameraPosition(
            { x: node.x + dist, y: node.y + dist * 0.35, z: node.z + dist * 0.6 },
            { x: node.x, y: node.y, z: node.z },
            600,
          )
          return
        }
        if (++attempts < 3) window.setTimeout(tryFly, 250)
      }
      tryFly()
    },
    [fgData],
  )

  const focusNode = useCallback((id: string) => flyTo(id, 28), [flyTo])

  // 重置视角：缩放到全图可见（zoomToFit 以原点为取景中心，图被 center 力约束在原点附近）
  const resetView = useCallback(() => {
    fgRef.current?.zoomToFit(600, 40)
  }, [])

  useImperativeHandle(ref, () => ({ focusNode, resetView, flyTo }), [focusNode, resetView, flyTo])

  // 定位请求 → 聚焦相机（依赖 data：展开岗位后节点才入画布，数据到位后再聚焦）
  useEffect(() => {
    if (focusRequest) focusNode(focusRequest.id)
  }, [focusRequest, data, focusNode])

  return (
    <div ref={containerRef} className={`relative ${className ?? 'h-full w-full'}`}>
      {dimensions.width > 0 && dimensions.height > 0 && (
        <ForceGraph3D
          ref={fgRef}
          width={dimensions.width}
          height={dimensions.height}
          graphData={fgData}
          backgroundColor={bgColor}
          nodeVal={(node: unknown) => nodeVal(node as GraphNode)}
          nodeThreeObject={getNodeObject}
          linkColor={() => linkColor}
          linkWidth={0.5}
          linkDirectionalParticles={2}
          linkDirectionalParticleWidth={2}
          linkDirectionalParticleSpeed={0.005}
          onNodeClick={handleNodeClick}
          onBackgroundClick={handleBackgroundClick}
          onNodeHover={handleNodeHover}
        />
      )}

      {/* B4：Hover Tooltip — 鼠标悬停节点时显示名称/类型/操作提示 */}
      {hoverInfo && (
        <div
          className="pointer-events-none absolute z-30 max-w-[200px] rounded-lg border border-border bg-canvas/95 px-3 py-2 text-xs shadow-lg backdrop-blur"
          style={{ left: hoverInfo.x + 14, top: Math.max(0, hoverInfo.y - 8) }}
        >
          <p className="truncate font-semibold text-ink">{hoverInfo.node.name}</p>
          <p className="mt-0.5 text-ink-muted">
            {hoverInfo.node.type === 'position'
              ? `岗位 · ${hoverInfo.node.status ?? 'candidate'}`
              : hoverInfo.node.type === 'skill'
                ? '技能节点'
                : '证据节点'}
          </p>
          <p className="mt-1 text-[10px] text-ink-faint">单击查看详情 · 双击展开/收起</p>
        </div>
      )}
    </div>
  )
})
