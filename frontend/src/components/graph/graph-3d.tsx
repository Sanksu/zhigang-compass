/**
 * 3D 力导向图组件 — 设计文档 §10.3 3D 可选模式
 *
 * 基于 react-force-graph-3d（Three.js WebGL 渲染）。
 *
 * 设计决策：
 * - 节点配色与 2D 完全一致（position 五状态机 / skill 墨色 / evidence 灰色）
 * - 暗色模式自动跟随（MutationObserver 监听 .dark 类）
 * - 容器尺寸由 ResizeObserver 自动追踪
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph3D from 'react-force-graph-3d'
import type { GraphData, GraphNode, NodeDetail, PositionStatus } from './types'

interface Graph3DProps {
  data: GraphData
  /** 已展开的岗位 id 集合（画布已只含这些岗位的技能，用于样式标记） */
  expandedPositions?: Set<string>
  onSelectNode: (node: NodeDetail | null) => void
  className?: string
}

/** 岗位状态机 → 颜色（与 Graph2D 一致） */
const COLOR_BY_STATUS: Record<PositionStatus, string> = {
  candidate: '#71717a',
  emerging: '#10b981',
  stable: '#3b82f6',
  declining: '#f59e0b',
  archived: '#ef4444',
}

const COLOR_EVIDENCE = '#a1a1aa'

function skillColor(dark: boolean): string {
  return dark ? '#fafafa' : '#09090b'
}

function nodeColor(node: GraphNode, dark: boolean): string {
  if (node.type === 'position') return COLOR_BY_STATUS[node.status ?? 'candidate']
  if (node.type === 'skill') return skillColor(dark)
  return COLOR_EVIDENCE
}

function nodeVal(node: GraphNode): number {
  const v = node.value ?? 30
  const base = node.type === 'position' ? 8 : node.type === 'skill' ? 5 : 3
  return base + (v / 100) * 8
}

function isDark(): boolean {
  return document.documentElement.classList.contains('dark')
}

export function Graph3D({ data, expandedPositions, onSelectNode, className }: Graph3DProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [dimensions, setDimensions] = useState({ width: 800, height: 640 })
  const [dark, setDark] = useState(isDark)

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

  // 单击 → 仅选中（展开/收起走详情面板按钮，与 2D 交互一致避免意图耦合）
  const handleClick = useCallback(
    (node: GraphNode | null) => {
      if (!node) {
        onSelectNode(null)
        return
      }
      onSelectNode({
        id: node.id,
        name: node.name,
        type: node.type,
        status: node.status,
        level: node.level,
        source: node.source,
        value: node.value,
        description: node.description,
      })
    },
    [onSelectNode],
  )

  // 点击空白区域清除选中
  const handleBackgroundClick = useCallback(() => {
    onSelectNode(null)
  }, [onSelectNode])

  return (
    <div ref={containerRef} className={className ?? 'h-full w-full'}>
      {dimensions.width > 0 && dimensions.height > 0 && (
        <ForceGraph3D
          width={dimensions.width}
          height={dimensions.height}
          graphData={fgData}
          backgroundColor={bgColor}
          nodeColor={(node: unknown) => nodeColor(node as GraphNode, dark)}
          nodeVal={(node: unknown) => {
            const n = node as GraphNode
            // 展开的岗位放大，提示其技能当前可见（可点击收起）
            const v = nodeVal(n)
            return expandedPositions?.has(n.id) ? v * 1.3 : v
          }}
          nodeLabel={(node: unknown) => {
            const n = node as GraphNode
            const lines = [n.name]
            if (n.type === 'position' && n.status) lines.push(`状态: ${n.status}`)
            if (n.type === 'skill' && n.level) lines.push(`级别: ${n.level}`)
            if (typeof n.value === 'number') lines.push(`权重: ${n.value}`)
            return lines.join(' · ')
          }}
          linkColor={() => linkColor}
          linkWidth={0.5}
          linkDirectionalParticles={2}
          linkDirectionalParticleWidth={2}
          linkDirectionalParticleSpeed={0.005}
          onNodeClick={handleClick}
          onBackgroundClick={handleBackgroundClick}
          nodeThreeObject={undefined}
        />
      )}
    </div>
  )
}
