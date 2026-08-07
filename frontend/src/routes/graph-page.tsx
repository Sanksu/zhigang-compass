import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { Box, Database, Network, Search } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Graph2D } from '@/components/graph/graph-2d'
import {
  NodeDetailPanel,
  type PositionDetail,
  type PrerequisiteItem,
  type SimilarSkillItem,
  type SkillCourseItem,
  type SkillDetail,
  type SkillEvidenceItem,
  type SkillPositionItem,
} from '@/components/graph/node-detail-panel'
import type { GraphData, GraphEdge, GraphNode, GraphViewType, NodeDetail } from '@/components/graph/types'
import { apiGet, ApiError } from '@/lib/api'

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
  techStack: 'Top 高频技能及其关联岗位（技能为中心）',
  level: '按级别（如中级）过滤的岗位-技能关系子图',
  positionCenter: '以高频岗位为中心展开岗位-技能关系',
}

/** WebGL2 可用性检测 — 不可用时 3D 按钮禁用，保持 2D 模式（设计文档 §6.3） */
function isWebGL2Available(): boolean {
  try {
    const canvas = document.createElement('canvas')
    return !!canvas.getContext('webgl2')
  } catch {
    return false
  }
}

// ============================================================
// 真实 API 数据适配：后端 /graph/panorama → GraphData
// ============================================================

interface PanoramaNode {
  id: string
  name: string
  type: string // position | skill
}

interface PanoramaEdge {
  source: string
  target: string
  weight: number
  necessity: string
  level: string
}

interface PanoramaData {
  nodes: PanoramaNode[]
  edges: PanoramaEdge[]
  stats: { nodes: number; edges: number }
}

/** 后端 panorama → 前端 GraphData（缺字段给默认值：岗位状态 stable、边关系 requires） */
function toGraphData(raw: PanoramaData): GraphData {
  const degree = new Map<string, number>()
  raw.edges.forEach((e) => {
    degree.set(e.source, (degree.get(e.source) ?? 0) + 1)
    degree.set(e.target, (degree.get(e.target) ?? 0) + 1)
  })

  const nodes: GraphNode[] = raw.nodes.map((n) => ({
    id: n.id,
    name: n.name,
    type: n.type === 'skill' ? 'skill' : 'position',
    value: degree.get(n.id) ?? 0,
    status: n.type === 'position' ? 'stable' : undefined,
  }))
  const edges: GraphEdge[] = raw.edges.map((e) => ({
    source: e.source,
    target: e.target,
    relation: 'requires',
    necessity: e.necessity === 'nice' ? 'nice' : 'must',
    weight: e.weight,
  }))
  return {
    nodes,
    edges,
    stats: {
      totalPositions: nodes.filter((n) => n.type === 'position').length,
      totalSkills: nodes.filter((n) => n.type === 'skill').length,
      totalEdges: edges.length,
      returnedNodes: nodes.length,
      totalNodesInGraph: nodes.length,
    },
  }
}

/** 非全景视图已由后端 /graph/view/{view_type} 提供（技术栈/级别/岗位中心均为服务端过滤）。 */

/**
 * 能力图谱页 — 设计文档 §10.3
 *
 * 数据来源：真实 API /api/v1/graph/panorama（Neo4j 聚合 + Redis 30s 缓存），
 * 视图切换在真实数据上本地派生（techStack/positionCenter 取首个岗位为中心子图）。
 * 已实现：2D ECharts 力导向图、四种视图切换、节点点击 + 详情面板、暗色模式。
 */
export function GraphPage() {
  const [view, setView] = useState<GraphViewType>('panorama')
  const [mode, setMode] = useState<'2d' | '3d'>('2d')
  const [selected, setSelected] = useState<NodeDetail | null>(null)
  const [raw, setRaw] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // 全文检索（GET /graph/search）
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<{ id: string; name: string; type: string; score: number }[]>([])
  const [searching, setSearching] = useState(false)
  // 技能节点详情（反向岗位 / 先修链 / 课程 / 证据 / 相似）
  const [skillDetail, setSkillDetail] = useState<SkillDetail | null>(null)
  const [skillEvidence, setSkillEvidence] = useState<SkillEvidenceItem[]>([])
  const [similarSkills, setSimilarSkills] = useState<SimilarSkillItem[]>([])
  // 岗位节点详情（GET /graph/position/{id}）
  const [positionDetail, setPositionDetail] = useState<PositionDetail | null>(null)

  // 视图切换 → 真实后端过滤（GET /graph/view/{view_type}），初始 panorama 同样走后端视图端点。
  // 切换视图不清 loading，数据到达后原子替换，避免闪屏。
  useEffect(() => {
    let cancelled = false
    apiGet<PanoramaData>(`/graph/view/${view}?limit=200`)
      .then((res) => {
        if (cancelled) return
        setRaw(toGraphData(res))
        setError(null)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? e.message : '图谱数据加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [view])

  // 选中技能节点 → 并行加载反向岗位 / 先修链 / 课程 / 证据 / 相似技能（真实 API）
  // 同步 loading 态由派生值 skillDetailView 表达，effect 内仅在异步回调中 setState
  useEffect(() => {
    if (!selected || selected.type !== 'skill') return
    let cancelled = false
    const sid = encodeURIComponent(selected.id)
    const positions = apiGet<{ positions: SkillPositionItem[] }>(`/graph/skill/${sid}/positions`)
      .then((r) => r.positions)
      .catch(() => [] as SkillPositionItem[])
    const prerequisites = apiGet<{ prerequisites: PrerequisiteItem[] }>(`/graph/skill/${sid}/prerequisites`)
      .then((r) => r.prerequisites)
      .catch(() => [] as PrerequisiteItem[])
    const courses = apiGet<{ courses: SkillCourseItem[] }>(`/graph/skill/${sid}/courses`)
      .then((r) => r.courses)
      .catch(() => [] as SkillCourseItem[])
    const evidence = apiGet<{ evidence: SkillEvidenceItem[] }>(`/graph/skill/${sid}/evidence`)
      .then((r) => r.evidence)
      .catch(() => [] as SkillEvidenceItem[])
    const similar = apiGet<{ similar: SimilarSkillItem[] }>(`/graph/skill/similar?skill_id=${sid}&top_k=6`)
      .then((r) => r.similar)
      .catch(() => [] as SimilarSkillItem[])
    Promise.all([positions, prerequisites, courses, evidence, similar]).then(([p, prereq, c, ev, sim]) => {
      if (!cancelled) {
        setSkillDetail({ skill_id: selected.id, positions: p, prerequisites: prereq, courses: c, loading: false })
        setSkillEvidence(ev)
        setSimilarSkills(sim)
      }
    })
    return () => {
      cancelled = true
    }
  }, [selected])

  // 选中岗位节点 → GET /graph/position/{id}（任职要求 + 必备/加分技能）。
  // 非 position 节点时不清空 positionDetail：渲染处按 selected 类型 + id 匹配派生过滤，
  // 避免 effect 内同步 setState（react-hooks/set-state-in-effect）。
  useEffect(() => {
    if (!selected || selected.type !== 'position') return
    let cancelled = false
    const pid = encodeURIComponent(selected.id)
    apiGet<PositionDetail>(`/graph/position/${pid}`)
      .then((d) => {
        if (!cancelled) setPositionDetail(d)
      })
      .catch(() => {
        if (!cancelled) setPositionDetail(null)
      })
    return () => {
      cancelled = true
    }
  }, [selected])

  // skill 详情视图：选中技能节点且详情已就绪（skill_id 匹配）时展示，否则视为加载中
  const skillDetailView: SkillDetail | null =
    selected?.type === 'skill'
      ? skillDetail && skillDetail.skill_id === selected.id && !skillDetail.loading
        ? skillDetail
        : { skill_id: '', positions: [], prerequisites: [], courses: [], loading: true }
      : null

  // 全文检索（设计文档 §5.4 cjk 全文索引）
  // 搜索序号：连续搜索时旧请求响应作废，避免慢响应覆盖新结果
  const searchSeqRef = useRef(0)
  function doSearch(q: string) {
    const term = q.trim()
    if (!term) {
      setSearchResults([])
      return
    }
    const seq = ++searchSeqRef.current
    setSearching(true)
    apiGet<{ items: { id: string; name: string; type: string; score: number }[]; total: number }>(
      `/graph/search?q=${encodeURIComponent(term)}&type=skill&size=8`,
    )
      .then((r) => {
        if (searchSeqRef.current === seq) setSearchResults(r.items)
      })
      .catch(() => {
        if (searchSeqRef.current === seq) setSearchResults([])
      })
      .finally(() => {
        if (searchSeqRef.current === seq) setSearching(false)
      })
  }

  // 点击搜索结果 / 相似技能 / 岗位必备技能 → 定位技能节点并选中
  // （detailStats 由选中节点实时计算，选中变化会自动触发详情面板重新加载）
  function focusSkill(id: string, name: string) {
    setSelected({ id, name, type: 'skill' })
  }

  // 视图数据即后端返回（四种视图均由 GET /graph/view/{view_type} 提供）
  const data = raw
  // WebGL2 不可用时 3D 按钮禁用，自动保持 2D（设计文档 §6.3 降级策略）
  const webgl2Available = useMemo(() => isWebGL2Available(), [])

  // 选中节点的关联统计（从当前视图数据中实时计算）
  const detailStats = useMemo(() => {
    if (!selected || !data) return undefined
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

  // 加载 / 错误 / 空态
  if (loading) {
    return (
      <Card className="h-[640px] flex items-center justify-center text-sm text-ink-muted">
        <div className="flex items-center gap-3">
          <div className="size-6 rounded-full border-2 border-ink border-t-transparent animate-spin" />
          正在加载图谱全景…
        </div>
      </Card>
    )
  }

  if (error) {
    return (
      <Card className="h-[640px] flex items-center justify-center text-sm text-state-archived">
        {error}（请确认后端服务与数据库已启动）
      </Card>
    )
  }

  if (!data || data.nodes.length === 0) {
    return (
      <Card className="h-[640px] flex items-center justify-center text-sm text-ink-muted">
        图谱暂无数据，请先通过数据管线导入 JD / 课程
      </Card>
    )
  }

  return (
    <>
      <PageHeader
        title="能力图谱"
        description="岗位-技能-证据关系可视化 · 2D 力导向图为主，3D 模式可选"
        actions={
          <div className="flex items-center gap-2">
            {/* 技能全文检索（真实 /graph/search） */}
            <div className="relative w-64">
              <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-ink-faint" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') doSearch(query)
                }}
                placeholder="搜索技能（如 Python）"
                className="h-8 pl-8 pr-16 text-xs"
              />
              <Button
                size="sm"
                variant="ghost"
                className="absolute right-0.5 top-0.5 h-7 px-2 text-xs"
                onClick={() => doSearch(query)}
                disabled={searching}
              >
                搜索
              </Button>
            </div>
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
                disabled={!webgl2Available}
                title={!webgl2Available ? '当前环境不支持 WebGL2，已降级 2D 模式' : undefined}
                className="h-7 px-2.5 text-xs"
              >
                3D
              </Button>
            </div>
          </div>
        }
      />

      {/* 搜索结果下拉 */}
      {searchResults.length > 0 && (
        <Card className="mb-3">
          <CardContent className="p-2">
            <ul className="divide-y divide-border">
              {searchResults.map((r) => (
                <li key={r.id}>
                  <button
                    onClick={() => focusSkill(r.id, r.name)}
                    className="flex w-full items-center justify-between gap-3 rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-subtle"
                  >
                    <span className="font-medium text-ink">{r.name}</span>
                    <span className="flex items-center gap-2 text-[10px] text-ink-faint">
                      <span className="rounded bg-subtle px-1 py-0.5 font-mono">技能</span>
                      <span className="font-mono">{(r.score * 100).toFixed(0)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

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
            {!webgl2Available && (
              <p className="text-[10px] text-ink-faint bg-canvas/80 backdrop-blur px-2 py-1 rounded border border-border">
                WebGL2 不可用，已降级 2D 模式
              </p>
            )}
          </div>
        </Card>

        {/* 节点详情面板 */}
        <Card className="h-[640px] overflow-hidden">
          <NodeDetailPanel
            node={selected}
            stats={detailStats}
            skillDetail={skillDetailView}
            positionDetail={selected?.type === 'position' && positionDetail && positionDetail.id === selected.id ? positionDetail : null}
            skillEvidence={selected && skillDetail && skillDetail.skill_id === selected.id ? skillEvidence : []}
            similarSkills={selected && skillDetail && skillDetail.skill_id === selected.id ? similarSkills : []}
            onSelectSkill={focusSkill}
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
