/**
 * 社区层级树（dendrogram）— 图算法优化方案阶段三：层次化提取可视化
 *
 * 数据源：GET /graph/algorithms/community-tree（Neo4j Community 节点，
 * scripts/sync_communities.py 同步；未同步时提示先运行同步脚本）。
 * ECharts tree 系列渲染：顶层为最粗社区，沿 NESTED_IN 递归展开至最细层，
 * 节点可折叠（expandAndCollapse）。暗色模式跟随 store theme 切换刷新。
 */
import { useEffect, useRef, useState } from 'react'
import { isDark } from '@/lib/utils'
import * as echarts from 'echarts'
import { GitFork, Loader2 } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useUIStore } from '@/store/ui'
import { apiGet } from '@/lib/api'
import type { components } from '@/types/api'

/** 契约 CommunityNode（社区层级树，backend/openapi/openapi.yaml 阶段三层次化提取 schema） */
type CommunityNode = components['schemas']['CommunityNode']

interface GraphCommunityTreeProps {
  className?: string
}

/** 暗色模式判定 — 跟随 documentElement 上的 .dark 类（与 graph-2d 同口径） */
export function GraphCommunityTree({ className }: GraphCommunityTreeProps) {
  const chartRef = useRef<HTMLDivElement>(null)
  const [tree, setTree] = useState<CommunityNode[] | null>(null)
  const [levels, setLevels] = useState<number[]>([])
  const [loading, setLoading] = useState(true)
  // 主题跟随：theme 变化时重建图表配色（依赖数组含 theme）
  const theme = useUIStore((s) => s.theme)

  // 加载社区层级树（懒加载一次，30s TTL 缓存）
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const r = await apiGet<{ tree: CommunityNode[]; levels: number[] }>(
          '/graph/algorithms/community-tree',
        )
        if (!cancelled) {
          setTree(r.tree)
          setLevels(r.levels)
        }
      } catch {
        /* 算法端点不可用时降级为空态（树保持 null），不阻塞图谱主功能 */
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  // ECharts tree 渲染（依赖 tree + theme，主题切换时重建配色）
  useEffect(() => {
    if (!chartRef.current || !tree || tree.length === 0) return
    const dark = isDark()
    const chart = echarts.init(chartRef.current)
    chart.setOption({
      tooltip: {
        trigger: 'item',
        triggerOn: 'mousemove',
        backgroundColor: dark ? '#1e293b' : '#fff',
        borderColor: dark ? '#334155' : '#e2e8f0',
        textStyle: { color: dark ? '#e2e8f0' : '#1e293b', fontSize: 11 },
        formatter: (p: { data: CommunityNode }) => {
          const d = p.data
          const skills = d.top_skills?.length ? d.top_skills.slice(0, 3).join('、') : '—'
          return `${d.name}<br/>层级 L${d.level} · ${d.cluster_count} 簇 · Q=${d.modularity?.toFixed(3) ?? '—'}<br/>代表技能：${skills}`
        },
      },
      series: [
        {
          type: 'tree',
          data: tree,
          layout: 'orthogonal',
          orient: 'LR',
          top: 12,
          left: 70,
          bottom: 12,
          right: 40,
          symbol: 'circle',
          symbolSize: 7,
          expandAndCollapse: true,
          initialTreeDepth: 2,
          label: {
            position: 'right',
            verticalAlign: 'middle',
            fontSize: 10,
            color: dark ? '#cbd5e1' : '#475569',
            formatter: (p: { data: CommunityNode }) => p.data.name,
          },
          leaves: { label: { position: 'right', verticalAlign: 'middle' } },
          lineStyle: { color: dark ? '#475569' : '#cbd5e1', width: 1 },
          itemStyle: { color: dark ? '#818cf8' : '#4f46e5' },
        },
      ],
    })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(chartRef.current)
    return () => {
      observer.disconnect()
      chart.dispose()
    }
  }, [tree, theme])

  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <GitFork className="size-4 text-ink-faint" />
          社区层级树
        </CardTitle>
        <CardDescription className="text-[11px]">
          Louvain 层次化提取 · {levels.length > 0 ? `${levels.length} 层（L0 最细 → L${levels[levels.length - 1]} 最粗）` : 'dendrogram'}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="flex items-center gap-2 py-6 text-xs text-ink-muted">
            <Loader2 className="size-3 animate-spin" />
            加载中…
          </div>
        ) : tree && tree.length > 0 ? (
          <div ref={chartRef} className="h-56 w-full" />
        ) : (
          <p className="py-6 text-xs text-ink-faint">
            社区层级索引未同步，请先运行{' '}
            <code className="rounded bg-subtle px-1 py-0.5">scripts/sync_communities.py</code>
          </p>
        )}
      </CardContent>
    </Card>
  )
}
