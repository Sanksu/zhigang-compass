import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Box, Loader2, Network, RotateCcw, Search, X } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Graph2D, type Graph2DHandle } from '@/components/graph/graph-2d'
import type { Graph3DHandle } from '@/components/graph/graph-3d'
import { GraphAnalysisPanel } from '@/components/graph/graph-analysis-panel'
import { GraphCommunityTree } from '@/components/graph/graph-community-tree'
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
import type { components } from '@/types/api'

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

/**
 * 后端 /graph/view/{view_type} 响应 data —— 契约 GraphViewData
 * （铁律一：单一事实源 backend/openapi/openapi.yaml）
 */
type PanoramaData = components['schemas']['GraphViewData']

const POSITION_STATUSES: GraphNode['status'][] = [
  'candidate',
  'emerging',
  'stable',
  'declining',
  'archived',
]

function isValidStatus(s?: string): s is NonNullable<GraphNode['status']> {
  return !!s && POSITION_STATUSES.includes(s as NonNullable<GraphNode['status']>)
}

/** 岗位中心视图自动展开的 Top 岗位数（首屏即呈现岗位-技能关系） */
const AUTO_EXPAND_COUNT = 6
/** 单个岗位展开的技能数上限（防止高频岗位技能全量涌入画布造成重叠） */
const MAX_SKILLS_PER_POSITION = 12

/** 后端 panorama → 前端 GraphData（岗位状态取自后端，缺省 candidate；边关系 requires） */
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
    status: n.type === 'position' ? (isValidStatus(n.status) ? n.status : 'candidate') : undefined,
  }))
  const edges: GraphEdge[] = raw.edges.map((e) => ({
    source: e.source,
    target: e.target,
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
      // 08-14 契约修复：全量节点数取后端 total_nodes（此前恒等于返回数，
      // 「已截断采样」提示为死代码）；后端未返回时回落返回数
      totalNodesInGraph: raw.stats?.total_nodes ?? nodes.length,
    },
  }
}

/** 非全景视图已由后端 /graph/view/{view_type} 提供（技术栈/级别/岗位中心均为服务端过滤）；
 *  画布岗位数上限（MAX_POSITIONS=30，见 visibleData）为前端展示层裁剪——高频岗位 Top-30
 *  保底显示 + 已展开岗位必显示，低频岗位经搜索/详情面板触达（2026-08-15 画布容量限制）。 */

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
  // 视图数据缓存（session 级，08-16 性能优化）：同视图切换回来不重复请求/转换。
  // 数据随每日 ETL 更新，session 内缓存可接受（页面刷新即失效）
  const viewCacheRef = useRef<Map<GraphViewType, GraphData>>(new Map())
  const [loading, setLoading] = useState(true)
  // 错误含业务码（08-14 审查：此前仅存 message，4040 与后端未启动混淆归因）
  const [error, setError] = useState<{ code: number; message: string } | null>(null)
  // 全文检索（GET /graph/search）
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<components['schemas']['SearchResultItem'][]>([])
  const [searching, setSearching] = useState(false)
  const [searchDone, setSearchDone] = useState(false)
  const searchBoxRef = useRef<HTMLDivElement>(null)
  // 技能节点详情（反向岗位 / 先修链 / 课程 / 证据 / 相似）
  const [skillDetail, setSkillDetail] = useState<SkillDetail | null>(null)
  const [skillEvidence, setSkillEvidence] = useState<SkillEvidenceItem[]>([])
  const [similarSkills, setSimilarSkills] = useState<SimilarSkillItem[]>([])
  // 岗位节点详情（GET /graph/position/{id}）
  const [positionDetail, setPositionDetail] = useState<PositionDetail | null>(null)
  // 展开的岗位 id 集合：点击岗位展开其技能（再点收起），多岗位独立展开
  const [expandedPositions, setExpandedPositions] = useState<Set<string>>(() => new Set())
  // 定位请求：搜索/相似技能点击后聚焦画布节点（含时间戳，连续点击同一技能也生效）
  const [focusRequest, setFocusRequest] = useState<{ id: string; ts: number } | null>(null)
  // 2D 画布命令句柄（重置视角）
  const graphRef = useRef<Graph2DHandle>(null)
  // 3D 画布命令句柄（聚焦/重置视角，与 2D 交互对齐）
  const graph3dRef = useRef<Graph3DHandle>(null)
  // 画布操作提示：首次访问显示，可手动关闭
  const [showOperationHint, setShowOperationHint] = useState(true)
  // 右侧面板 Tab：节点详情 / 算法分析
  const [rightTab, setRightTab] = useState<'detail' | 'analysis'>('detail')
  // 视图数据即后端返回（四种视图均由 GET /graph/view/{view_type} 提供），
  // 声明在 useCallback 依赖之前：focusSkill 等事件处理器需读取当前数据
  const data = raw

  const togglePosition = useCallback((id: string) => {
    setExpandedPositions((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  // 视图切换 → 真实后端过滤（GET /graph/view/{view_type}），初始 panorama 同样走后端视图端点。
  // 切换视图不清 loading，数据到达后原子替换，避免闪屏。
  // 展开状态在 Tabs 事件回调中同步清空（effect 内 setState 会触发 cascading renders）
  // limit=120：techStack 全量渲染技能节点，节点数与 limit 线性相关（120→约 166 节点），
  // 控制画布规模在 ECharts force 布局可承受范围，避免主线程长时间阻塞（2026-08-08）
  useEffect(() => {
    let cancelled = false
    // 数据到位后统一应用：设置数据 + 非技术栈视图自动展开 Top 岗位 + 结束 loading
    const applyViewData = (g: GraphData) => {
      setRaw(g)
      setError(null)
      if (view !== 'techStack') {
        const top = g.nodes
          .filter((n) => n.type === 'position')
          .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
          .slice(0, AUTO_EXPAND_COUNT)
          .map((n) => n.id)
        setExpandedPositions(new Set(top))
      }
      setLoading(false)
    }
    const cached = viewCacheRef.current.get(view)
    if (cached) {
      // 命中缓存：跳过网络请求与转换（08-16 性能优化）
      applyViewData(cached)
      return
    }
    apiGet<PanoramaData>(`/graph/view/${view}?limit=120`)
      .then((res) => {
        if (cancelled) return
        const g = toGraphData(res)
        viewCacheRef.current.set(view, g)
        applyViewData(g)
      })
      .catch((e) => {
        if (!cancelled) {
          setError(
            e instanceof ApiError
              ? { code: e.code, message: e.message }
              : { code: 0, message: '图谱数据加载失败' },
          )
        }
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
      setSearchDone(false)
      return
    }
    setSearchDone(true)
    const seq = ++searchSeqRef.current
    setSearching(true)
    apiGet<components['schemas']['SearchResultsData']>(
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

  // 搜索下拉：点击外部关闭
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (searchBoxRef.current && !searchBoxRef.current.contains(e.target as Node)) {
        setSearchResults([])
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // 点击搜索结果 / 相似技能 / 岗位必备技能 → 选中技能节点 + 定位画布
  // 岗位中心视图下技能需先展开其所属岗位（从全量数据边中找一个关联岗位展开），
  // 再触发 Graph2D.focusNode 居中高亮；技术栈视图技能已全量展示，直接聚焦。
  const focusSkill = useCallback(
    (id: string, name: string) => {
      setSelected({ id, name, type: 'skill' })
      if (view !== 'techStack' && data) {
        const edge = data.edges.find((e) => e.source === id || e.target === id)
        const pid = edge ? (edge.source === id ? edge.target : edge.source) : undefined
        if (pid) {
          setExpandedPositions((prev) => {
            if (prev.has(pid)) return prev
            const next = new Set(prev)
            next.add(pid)
            return next
          })
        }
      }
      setFocusRequest((prev) => ({ id, ts: (prev?.ts ?? 0) + 1 }))
    },
    [view, data],
  )

  // 画布可见数据：
  // - techStack（技能为中心）：全量展示技能+边，不做岗位过滤
  // - 岗位中心视图：展示岗位节点 + 已展开岗位的技能（单岗位技能数上限防重叠）
  // 岗位显示数量限制（Top-30 按关联度降序 + 展开的岗位必显示，2026-08-15）：
  // 岗位中心/技术栈视图岗位全量 100+，物理上放不下岗位防重叠所需间距
  // （enforceSpread minGap 60 → 每岗位约 1.06 万 px²，画布仅 ~54 万 px² 容量 ~30 岗位），
  // 且全量渲染节点爆炸不可读。低频岗位不显示，可在搜索/详情面板中触达。
  const MAX_POSITIONS = 30
  const visibleData = useMemo<GraphData | null>(() => {
    if (!data) return null

    const keepPositions = new Set<string>()
    data.nodes
      .filter((n) => n.type === 'position')
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
      .slice(0, MAX_POSITIONS)
      .forEach((p) => keepPositions.add(p.id))
    expandedPositions.forEach((id) => keepPositions.add(id))

    if (view === 'techStack') {
      const nodes = data.nodes.filter((n) => n.type !== 'position' || keepPositions.has(n.id))
      const nodeIds = new Set(nodes.map((n) => n.id))
      const edges = data.edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
      return { ...data, nodes, edges }
    }

    // 每个展开岗位：按边权重取 Top-N 技能（多岗位共享技能去重）
    const perPositionSkills = new Map<string, string[]>()
    for (const pid of expandedPositions) {
      const ranked = data.edges
        .filter((e) => e.source === pid || e.target === pid)
        .sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0))
      const skills: string[] = []
      for (const e of ranked) {
        if (skills.length >= MAX_SKILLS_PER_POSITION) break
        const sid = e.source === pid ? e.target : e.source
        if (!skills.includes(sid)) skills.push(sid)
      }
      perPositionSkills.set(pid, skills)
    }
    const skillIds = new Set([...perPositionSkills.values()].flat())
    const nodes = data.nodes.filter((n) =>
      n.type === 'position' ? keepPositions.has(n.id) : skillIds.has(n.id),
    )
    // 只保留两端都可见的边（岗位-技能关系，且岗位在显示集、技能在展开上限内）
    const edges = data.edges.filter((e) => {
      const a = keepPositions.has(e.source)
      const b = keepPositions.has(e.target)
      return (a && skillIds.has(e.target)) || (b && skillIds.has(e.source))
    })
    return { ...data, nodes, edges }
  }, [data, view, expandedPositions])
  // WebGL2 不可用时 3D 按钮禁用，自动保持 2D（设计文档 §6.3 降级策略）
  const webgl2Available = useMemo(() => isWebGL2Available(), [])
  // 触控设备（移动/平板，粗指针）固定 2D 模式（设计文档 §6.3：平板/移动端固定 2D）
  const isCoarsePointer = useMemo(() => window.matchMedia('(pointer: coarse)').matches, [])
  /** 3D 模式是否被锁定（WebGL2 不可用或触控设备） */
  const mode3dLocked = !webgl2Available || isCoarsePointer

  // 选中节点的关联统计（从当前视图数据中实时计算）+ 全图最大关联度（详情条归一化基准）
  const detailStats = useMemo(() => {
    if (!selected || !data) return undefined
    const linkedIds = new Set<string>()
    data.edges.forEach((e) => {
      if (e.source === selected.id) linkedIds.add(e.target)
      if (e.target === selected.id) linkedIds.add(e.source)
    })
    const linked = data.nodes.filter((n) => linkedIds.has(n.id))
    const maxValue = Math.max(1, ...data.nodes.map((n) => n.value ?? 0))
    return {
      positionCount: linked.filter((n) => n.type === 'position').length,
      skillCount: linked.filter((n) => n.type === 'skill').length,
      evidenceCount: linked.filter((n) => n.type === 'evidence').length,
      maxValue,
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
        {error.message}
        {error.code === 4040 ? '（未找到对应数据）' : '（请确认后端服务与数据库已启动）'}
      </Card>
    )
  }

  if (!data || data.nodes.length === 0) {
    return (
      <Card className="h-[640px] flex items-center justify-center text-sm text-ink-muted">
        图谱暂无数据，请稍后再试，或联系管理员导入数据
      </Card>
    )
  }

  return (
    <>
      <PageHeader
        title="能力图谱"
        description="岗位-技能关系可视化 · 默认 2D 力导向图便于分析，3D 模式用于沉浸式浏览"
        actions={
          <div className="flex items-center gap-2">
            {/* 技能全文检索（真实 /graph/search） */}
            <div ref={searchBoxRef} className="relative w-64">
              <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-ink-faint" />
              <Input
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value)
                  if (!e.target.value.trim()) {
                    setSearchResults([])
                    setSearchDone(false)
                  }
                }}
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
                {searching ? <Loader2 className="size-3 animate-spin" /> : '搜索'}
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
                disabled={mode3dLocked}
                title={isCoarsePointer ? '触控设备固定 2D 模式（设计文档 §6.3）' : !webgl2Available ? '当前环境不支持 WebGL2，已降级 2D 模式' : '3D 沉浸式浏览（节点带空间纵深，适合展示整体结构）'}
                className="h-7 px-2.5 text-xs"
              >
                3D
              </Button>
            </div>
          </div>
        }
      />

      {/* 搜索结果下拉 */}
      {(searchResults.length > 0 || (searchDone && !searching && query.trim())) && (
        <Card className="mb-3">
          <CardContent className="p-2">
            {searchResults.length > 0 ? (
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
            ) : (
              <p className="px-2 py-2 text-xs text-ink-muted">未找到与“{query.trim()}”相关的技能</p>
            )}
          </CardContent>
        </Card>
      )}

      {/* 视图切换 tabs */}
      <Tabs
        value={view}
        onValueChange={(v) => {
          // 视图切换：同步清空展开的岗位（新视图技能集不同），再切换数据
          setExpandedPositions(new Set())
          setView(v as GraphViewType)
        }}
      >
        <div className="flex items-center justify-between gap-4 mb-3">
          <TabsList>
            {(Object.keys(VIEW_LABEL) as GraphViewType[]).map((v) => (
              <TabsTrigger key={v} value={v} className="text-xs" title={VIEW_DESC[v]}>
                {VIEW_LABEL[v]}
              </TabsTrigger>
            ))}
          </TabsList>

          {/* 数据规模指示 */}
          <div className="flex items-center gap-3 text-xs text-ink-muted">
            <span className="flex items-center gap-1" title="当前视图节点数 / 图谱总节点数">
              <Network className="size-3" />
              <span className="font-mono tabular-nums">{data.stats.returnedNodes}</span>
              <span className="text-ink-faint">/ {data.stats.totalNodesInGraph}</span>
              <span className="text-ink-faint">节点</span>
            </span>
            <span className="flex items-center gap-1" title="当前视图边数">
              <Box className="size-3" />
              <span className="font-mono tabular-nums">{data.stats.totalEdges}</span>
              <span className="text-ink-faint">边</span>
            </span>
            {visibleData && (
              <span className="hidden sm:flex items-center gap-1" title="当前视图节点构成">
                <span className="font-mono tabular-nums">
                  {visibleData.nodes.filter((n) => n.type === 'position').length}
                </span>
                <span className="text-ink-faint">岗位</span>
                <span className="text-ink-faint">·</span>
                <span className="font-mono tabular-nums">
                  {visibleData.nodes.filter((n) => n.type === 'skill').length}
                </span>
                <span className="text-ink-faint">技能</span>
              </span>
            )}
            {data.stats.returnedNodes < data.stats.totalNodesInGraph && (
              <span className="text-ink-faint text-[10px]">已截断采样</span>
            )}
          </div>
        </div>
      </Tabs>

      {/* 画布 + 详情面板：画布占 70-75%，详情占 25-30% */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-4">
        <Card className="relative overflow-hidden h-[640px]">
          {/* 重置视角（roam 平移/缩放后一键回初始视角；3D 模式对应缩放到全图） */}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => (mode === '3d' ? graph3dRef.current?.resetView() : graphRef.current?.resetView())}
            className="absolute right-2 top-2 z-10 h-7 px-2 text-xs text-ink-muted hover:text-ink"
            title="重置视角"
          >
            <RotateCcw className="size-3 mr-1" />
            重置视角
          </Button>
          {mode === '2d' ? (
            <Graph2D
              ref={graphRef}
              data={visibleData!}
              expandedPositions={expandedPositions}
              selectedId={selected?.id ?? null}
              focusRequest={focusRequest}
              onSelectNode={setSelected}
              onTogglePosition={togglePosition}
              className="h-full w-full"
            />
          ) : (
            <Suspense fallback={<div className="flex h-full w-full items-center justify-center text-sm text-ink-muted">加载 3D 渲染引擎…</div>}>
              <Graph3D
                ref={graph3dRef}
                data={visibleData!}
                expandedPositions={expandedPositions}
                selectedId={selected?.id ?? null}
                focusRequest={focusRequest}
                onSelectNode={setSelected}
                onTogglePosition={togglePosition}
                className="h-full w-full"
              />
            </Suspense>
          )}
          {/* 画布操作提示（可关闭） */}
          {showOperationHint && (
            <div className="absolute top-14 right-2 z-10 max-w-[180px] rounded-md border border-border bg-canvas/90 backdrop-blur px-2.5 py-2 shadow-sm">
              <div className="flex items-start justify-between gap-2">
                <span className="text-[11px] font-medium text-ink-secondary">操作提示</span>
                <button
                  onClick={() => setShowOperationHint(false)}
                  className="rounded-sm text-ink-faint hover:text-ink hover:bg-subtle"
                  aria-label="关闭操作提示"
                >
                  <X className="size-3" />
                </button>
              </div>
              <ul className="mt-1.5 space-y-0.5 text-[10px] text-ink-muted">
                {mode === '3d' ? (
                  <>
                    <li>滚轮缩放 · 拖拽空白旋转视角</li>
                    <li>拖拽节点调整位置</li>
                    <li>单击节点查看详情</li>
                    <li>双击岗位展开/收起技能</li>
                  </>
                ) : (
                  <>
                    <li>滚轮缩放 · 拖拽空白平移</li>
                    <li>拖拽节点调整位置</li>
                    <li>单击节点查看详情</li>
                    <li>双击岗位展开/收起技能</li>
                  </>
                )}
              </ul>
            </div>
          )}

          {/* 视图说明 */}
          <div className="absolute bottom-3 left-3 right-3 flex items-center justify-between gap-3 pointer-events-none">
            <p className="text-[11px] text-ink-muted bg-canvas/80 backdrop-blur px-2 py-1 rounded border border-border">
              {VIEW_DESC[view]}
            </p>
            {view !== 'techStack' && (
              <p className="text-[11px] text-ink-muted bg-canvas/80 backdrop-blur px-2 py-1 rounded border border-border">
                已展开 {expandedPositions.size} 个岗位
              </p>
            )}
            {mode3dLocked && (
              <p className="text-[10px] text-ink-faint bg-canvas/80 backdrop-blur px-2 py-1 rounded border border-border">
                {isCoarsePointer ? '触控设备固定 2D 模式' : 'WebGL2 不可用，已降级 2D 模式'}
              </p>
            )}
          </div>
        </Card>

        {/* 节点详情面板 + 图谱算法分析：用 Tab 分隔，避免信息过载 */}
        <Tabs value={rightTab} onValueChange={(v) => setRightTab(v as 'detail' | 'analysis')} className="flex flex-col h-[640px]">
          <Card className="flex flex-col h-full overflow-hidden">
            <TabsList className="mx-3 mt-3 grid w-auto grid-cols-2">
              <TabsTrigger value="detail" className="text-xs">
                节点详情
              </TabsTrigger>
              <TabsTrigger value="analysis" className="text-xs">
                算法分析
              </TabsTrigger>
            </TabsList>
            <TabsContent value="detail" className="flex-1 overflow-y-auto mt-0 px-0 py-0">
              <NodeDetailPanel
                node={selected}
                stats={detailStats}
                skillDetail={skillDetailView}
                positionDetail={selected?.type === 'position' && positionDetail && positionDetail.id === selected.id ? positionDetail : null}
                skillEvidence={selected && skillDetail && skillDetail.skill_id === selected.id ? skillEvidence : []}
                similarSkills={selected && skillDetail && skillDetail.skill_id === selected.id ? similarSkills : []}
                positionExpanded={selected?.type === 'position' ? expandedPositions.has(selected.id) : false}
                onTogglePosition={togglePosition}
                onSelectSkill={focusSkill}
                onClose={() => setSelected(null)}
              />
            </TabsContent>
            <TabsContent value="analysis" className="flex-1 overflow-y-auto mt-0 px-0 py-0">
              <GraphAnalysisPanel
                skills={data.nodes.filter((n) => n.type === 'skill').map((n) => ({ id: n.id, name: n.name }))}
                onFocusSkill={focusSkill}
              />
              <GraphCommunityTree className="mt-3" />
            </TabsContent>
          </Card>
        </Tabs>
      </div>

      {/* 图例：与画布实际渲染对齐（形状+颜色，支持色盲识别） */}
      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-muted" role="list" aria-label="图谱图例">
        <span className="font-medium text-ink-secondary">图例：</span>
        <span className="flex items-center gap-1.5" role="listitem">
          <span className="size-2.5 rounded-full bg-state-active" role="img" aria-label="活跃岗位：蓝灰圆形" /> 活跃
        </span>
        <span className="flex items-center gap-1.5" role="listitem">
          <span className="size-2.5 rounded-full bg-state-stable" role="img" aria-label="稳定岗位：蓝色圆形" /> 稳定
        </span>
        <span className="flex items-center gap-1.5" role="listitem">
          <span
            className="size-2.5 bg-state-emerging"
            style={{ clipPath: 'polygon(50% 0%, 0% 100%, 100% 100%)' }}
            role="img"
            aria-label="新兴岗位：绿色三角形"
          /> 新兴
        </span>
        <span className="flex items-center gap-1.5" role="listitem">
          <span className="size-2.5 rounded-full bg-state-candidate" role="img" aria-label="候选岗位：灰色圆形" /> 候选
        </span>
        <span className="flex items-center gap-1.5" role="listitem">
          <span className="size-2.5 bg-state-declining" role="img" aria-label="衰退岗位：橙色矩形" /> 衰退
        </span>
        <span className="flex items-center gap-1.5" role="listitem">
          <span className="size-2.5 rounded-md bg-state-archived" role="img" aria-label="归档岗位：红色圆角矩形" /> 归档
        </span>
        <span className="flex items-center gap-1.5" role="listitem">
          <span className="size-2.5 rounded-full bg-[#09090b] dark:bg-[#fafafa]" role="img" aria-label="技能节点" /> 技能
        </span>
        <span className="flex items-center gap-1.5" role="listitem">
          <span className="w-4 h-0.5 bg-ink/60" aria-hidden="true" />
          岗位-技能
        </span>
        <span className="flex items-center gap-1.5" role="listitem">
          <span className="w-4 h-1 bg-ink/60" aria-hidden="true" />
          关联强度
        </span>
      </div>
    </>
  )
}
