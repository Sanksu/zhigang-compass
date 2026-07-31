import { lazy, Suspense, useMemo, useState } from 'react'
import { Box, Database, Network } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Graph2D } from '@/components/graph/graph-2d'
import { NodeDetailPanel } from '@/components/graph/node-detail-panel'
import { getMockGraphData } from '@/components/graph/mock-data'
import type { GraphViewType, NodeDetail } from '@/components/graph/types'

/** 3D 图谱懒加载 — Three.js 约 1.4MB，仅在用户点击"3D"时按需加载 */
const Graph3D = lazy(() => import('@/components/graph/graph-3d').then((m) => ({ default: m.Graph3D })))

const VIEW_LABEL: Record<GraphViewType, string> = {
  panorama: '全景视图',
  techStack: '技术栈视图',
  level: '级别视图',
  positionCenter: '岗位中心',
}

const VIEW_DESC: Record<GraphViewType, string> = {
  panorama: 'Top-N 高频岗位及其关联技能',
  techStack: 'Louvain 聚类后选中技能簇的子图',
  level: '按级别（如中级）过滤的岗位-技能关系子图',
  positionCenter: '以「前端开发工程师」为中心 2-hop 展开',
}

/**
 * 能力图谱页 — 设计文档 §10.3
 *
 * 当前阶段（M3 前端提前启动）：
 * - 数据来源：本地 mock（mock-data.ts），后端 /api/v1/graph/panorama 就绪后改用真实接口
 * - 已实现：2D ECharts 力导向图、四种视图切换、节点点击 + 详情面板、暗色模式
 * - 待实现（M3 后续）：真实 API 接入、min_weight/focus 参数过滤
 */
export function GraphPage() {
  const [view, setView] = useState<GraphViewType>('panorama')
  const [mode, setMode] = useState<'2d' | '3d'>('2d')
  const [selected, setSelected] = useState<NodeDetail | null>(null)

  const data = useMemo(() => getMockGraphData(view), [view])

  // 选中节点的关联统计（从当前视图数据中实时计算）
  const detailStats = useMemo(() => {
    if (!selected) return undefined
    const linkedIds = new Set<string>()
    data.edges.forEach((e) => {
      if (e.source === selected.id) linkedIds.add(e.target)
      if (e.target === selected.id) linkedIds.add(e.source)
    })
    const linked = data.nodes.filter((n) => linkedIds.has(n.id))
    return {
      positionCount: linked.filter((n) => n.type === 'position').length,
      skillCount: linked.filter((n) => n.type === 'skill').length,
      evidenceCount: linked.filter((n) => n.type === 'evidence').length,
    }
  }, [selected, data])

  return (
    <>
      <PageHeader
        title="能力图谱"
        description="岗位-技能-证据关系可视化 · 2D 力导向图为主，3D 模式可选"
        actions={
          <div className="flex items-center gap-1 rounded-md border border-border p-0.5">
            <Button
              size="sm"
              variant={mode === '2d' ? 'default' : 'ghost'}
              onClick={() => setMode('2d')}
              className="h-7 px-2.5 text-xs"
            >
              2D
            </Button>
            <Button
              size="sm"
              variant={mode === '3d' ? 'default' : 'ghost'}
              onClick={() => setMode('3d')}
              className="h-7 px-2.5 text-xs"
            >
              3D
            </Button>
          </div>
        }
      />

      {/* 视图切换 tabs */}
      <Tabs value={view} onValueChange={(v) => setView(v as GraphViewType)}>
        <div className="flex items-center justify-between gap-4 mb-3">
          <TabsList>
            {(Object.keys(VIEW_LABEL) as GraphViewType[]).map((v) => (
              <TabsTrigger key={v} value={v} className="text-xs">
                {VIEW_LABEL[v]}
              </TabsTrigger>
            ))}
          </TabsList>

          {/* 数据规模指示 */}
          <div className="flex items-center gap-3 text-xs text-ink-muted">
            <span className="flex items-center gap-1">
              <Network className="size-3" />
              <span className="font-mono tabular-nums">{data.stats.returnedNodes}</span>
              <span className="text-ink-faint">/ {data.stats.totalNodesInGraph}</span>
              <span className="text-ink-faint">节点</span>
            </span>
            <span className="flex items-center gap-1">
              <Box className="size-3" />
              <span className="font-mono tabular-nums">{data.stats.totalEdges}</span>
              <span className="text-ink-faint">边</span>
            </span>
            <span className="text-ink-faint text-[10px]">≤ 600 / 1500 上限</span>
          </div>
        </div>
      </Tabs>

      {/* 画布 + 详情面板：画布占 70-75%，详情占 25-30% */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-4">
        <Card className="relative overflow-hidden h-[640px]">
          {mode === '2d' ? (
            <Graph2D
              data={data}
              selectedId={selected?.id ?? null}
              onSelectNode={setSelected}
              className="h-full w-full"
            />
          ) : (
            <Suspense fallback={<div className="flex h-full w-full items-center justify-center text-sm text-ink-muted">加载 3D 渲染引擎…</div>}>
              <Graph3D
                data={data}
                onSelectNode={setSelected}
                className="h-full w-full"
              />
            </Suspense>
          )}
          {/* 视图说明 */}
          <div className="absolute bottom-3 left-3 right-3 flex items-center justify-between gap-3 pointer-events-none">
            <p className="text-[11px] text-ink-muted bg-canvas/80 backdrop-blur px-2 py-1 rounded border border-border">
              {VIEW_DESC[view]}
            </p>
            <p className="text-[10px] text-ink-faint bg-canvas/80 backdrop-blur px-2 py-1 rounded border border-border font-mono">
              mock · 后端就绪后切真实 API
            </p>
          </div>
        </Card>

        {/* 节点详情面板 */}
        <Card className="h-[640px] overflow-hidden">
          <NodeDetailPanel
            node={selected}
            stats={detailStats}
            onClose={() => setSelected(null)}
          />
        </Card>
      </div>

      {/* 图例 */}
      <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-ink-muted">
        <span className="font-medium text-ink-secondary">图例：</span>
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-full bg-state-stable" /> 稳定岗位
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-full bg-state-emerging" /> 新兴岗位
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-full bg-state-candidate" /> 候选岗位
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-full bg-state-declining" /> 衰退岗位
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-full bg-state-archived" /> 归档岗位
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-full bg-ink" /> 技能
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 bg-ink-faint" style={{ transform: 'rotate(45deg)' }} /> 证据
        </span>
        <span className="flex items-center gap-1.5">
          <Database className="size-3" />
          <span>实线=requires · 虚线=proves</span>
        </span>
      </div>
    </>
  )
}
