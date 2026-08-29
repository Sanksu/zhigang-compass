import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Box, Crosshair, Loader2, Maximize2, Minimize2, Network, RotateCcw, Search, X } from 'lucide-react'
import { useUIStore } from '@/store/ui'
import { PageHeader } from '@/components/layout/page-header'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  POSITION_STATE_DOT,
  POSITION_STATE_META,
  type PositionState,
} from '@/components/shared/position-state-badge'
import { Graph2D, type Graph2DHandle } from '@/components/graph/graph-2d'
import type { Graph3DHandle } from '@/components/graph/graph-3d'
import { GraphAnalysisPanel } from '@/components/graph/graph-analysis-panel'
import { GraphCommunityTree } from '@/components/graph/graph-community-tree'
import { GraphDetailRail } from '@/components/graph/graph-detail-rail'
import { toGraphData } from '@/components/graph/graph-adapter'
import { aggregateByDomain, buildDomainView } from '@/components/graph/graph-domain'
import { EvolutionTimeline, type EvolutionMarks } from '@/components/graph/evolution-timeline'
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
import type { GraphData, GraphEdge, GraphViewType, NodeDetail } from '@/components/graph/types'
import { SKILL_CATEGORY_PALETTE } from '@/components/graph/graph-visual-tokens'
import type { LearningPathItem } from '@/components/match/types'
import type { LearningStatus } from '@/components/learning/learning-timeline'
import { apiGet, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { components } from '@/types/api'

/** 3D 图谱懒加载 — Three.js 约 1.4MB，仅在用户点击"3D"时按需加载 */
const Graph3D = lazy(() => import('@/components/graph/graph-3d').then((m) => ({ default: m.Graph3D })))

type PortraitEvidenceData = components['schemas']['PortraitEvidenceData']

/** 画像 attr 节点 → 证据维度（子条目按 id 前缀，大类按名称） */
function portraitDimension(node: NodeDetail): 'salary' | 'experience' | 'education' | null {
  if (node.id.startsWith('sal_')) return 'salary'
  if (node.id.startsWith('exp_')) return 'experience'
  if (node.id.startsWith('edu_')) return 'education'
  if (node.id.startsWith('attr_')) {
    if (node.name === '薪资') return 'salary'
    if (node.name === '经验') return 'experience'
    if (node.name === '学历') return 'education'
  }
  return null
}

/** 画像子条目 → 条目标签（'1-1.3万 ×9' → '1-1.3万'；大类节点返回空=全维度） */
function portraitLabel(node: NodeDetail): string {
  if (!/^(sal|exp|edu)_/.test(node.id)) return ''
  return node.name.replace(/\s*×\d+$/, '').trim()
}

/** 页面实际渲染的视图（08-29 视图收敛：level 与 positionCenter 同查询同数据、
 *  无差异化过滤，页签移除；positionCenter 端点保留作岗位画像下拉数据源） */
type GraphTab = Extract<GraphViewType, 'panorama' | 'techStack' | 'positionPortrait'>

const VIEW_LABEL: Record<GraphTab, string> = {
  panorama: '全景视图',
  techStack: '技术栈视图',
  positionPortrait: '岗位画像',
}

const VIEW_DESC: Record<GraphTab, string> = {
  // 08-22 域聚合下钻（#412）：panorama 不再是 Top-N 裁剪，全部岗位以域超节点常驻
  panorama: '全岗位按职能域聚合常驻 · 双击域展开岗位，双击岗位展开技能',
  techStack: 'Top 高频技能及其关联岗位（技能为中心）',
  positionPortrait: '单岗位画像：薪资/经验等属性维度 + 技能要求（下拉切换岗位）',
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
// 真实 API 数据适配：后端 /graph/view/{view_type} → GraphData
// ============================================================

/**
 * 后端 /graph/view/{view_type} 响应 data —— 契约 GraphViewData
 * （铁律一：单一事实源 backend/openapi/openapi.yaml）
 */
type PanoramaData = components['schemas']['GraphViewData']

/** 单个岗位展开的技能数上限（防止高频岗位技能全量涌入画布造成重叠） */
const MAX_SKILLS_PER_POSITION = 12

/**
 * 演示视角书签（答辩用）：点击后镜头平滑飞行到锚定岗位，避免现场手动拖拽找簇。
 * 力导向每次布局坐标不同，故只存节点名/ id，坐标由画布运行时解析；
 * 当前视图数据中不存在的锚点对应按钮自动隐藏。
 * 注意：name 须与图谱数据中的岗位 name 完全一致——赛前按最新数据核对一次。
 */
const DEMO_BOOKMARKS: { label: string; nodeName: string }[] = [
  { label: '算法簇', nodeName: '算法工程师' },
  { label: '大模型簇', nodeName: '大模型算法工程师' },
  { label: '数据簇', nodeName: '数据分析师' },
  { label: '前端簇', nodeName: '前端开发工程师' },
]

/** 非全景视图均由后端 /graph/view/{view_type} 服务端过滤返回，前端仅做展示层裁剪。 */

/**
 * 能力图谱页 — 设计文档 §10.3
 *
 * 数据来源：真实 API /api/v1/graph/view/{view_type}（Neo4j 聚合 + Redis 缓存，
 * 各视图均为服务端过滤）。已实现：2D ECharts 力导向图、视图切换、
 * 节点点击 + 详情面板、暗色模式。
 */
export function GraphPage() {
  const [view, setView] = useState<GraphTab>('panorama')
  const [mode, setMode] = useState<'2d' | '3d'>('2d')
  const [selected, setSelected] = useState<NodeDetail | null>(null)
  const [raw, setRaw] = useState<GraphData | null>(null)
  // 岗位画像（positionPortrait）：选中的岗位 id + 下拉选项。
  // 选项取自 /graph/view/positionCenter 的岗位节点（freq 降序），存独立 state
  // （不进 viewCacheRef——那里缓存的是画布 GraphData，选项集非画布数据）
  const [portraitPosition, setPortraitPosition] = useState('')
  const [portraitOptions, setPortraitOptions] = useState<
    { id: string; name: string; domainId: string; domainName: string }[]
  >([])
  // 岗位簇（职能域）两级下拉：簇选中后岗位选项联动过滤
  const [portraitCluster, setPortraitCluster] = useState('')
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
  // 展开的职能域 id 集合（panorama 聚合下钻第二级）：双击域超节点展开域内岗位
  const [expandedDomains, setExpandedDomains] = useState<Set<string>>(() => new Set())
  // 演化时间轴标记（P0-2）：滑到某版本 → 本版新增/消亡节点画布打标
  const [evolutionMarks, setEvolutionMarks] = useState<EvolutionMarks | null>(null)
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

  const toggleDomain = useCallback((id: string) => {
    setExpandedDomains((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  // 岗位画像 attr 节点（薪资/经验/学历大类与条目）：画像视图下选中后展示
  // 证据 JD（/graph/position/{id}/portrait-evidence）；其他视图 attr 无详情
  // 端点，仍不选中。setSelected 稳定 → 回调引用稳定，Graph2D 挂载 effect
  // 不因回调换引用而反复重建图表
  const handleSelectNode = useCallback((node: NodeDetail | null) => {
    if (node && (node.type !== 'attr' || view === 'positionPortrait')) setSelected(node)
  }, [view])

  // 视图切换 → 真实后端过滤（GET /graph/view/{view_type}），初始 panorama 同样走后端视图端点。
  // 切换视图不清 loading，数据到达后原子替换，避免闪屏。
  // 展开状态在 Tabs 事件回调中同步清空（effect 内 setState 会触发 cascading renders）
  // limit=120：techStack 全量渲染技能节点，节点数与 limit 线性相关（120→约 166 节点），
  // 控制画布规模在 ECharts force 布局可承受范围，避免主线程长时间阻塞（2026-08-08）
  useEffect(() => {
    // 岗位画像走独立数据流（下方 effect：按选中岗位请求，绕过 viewCacheRef），
    // 不进入本 effect 的统一视图请求/缓存逻辑
    if (view === 'positionPortrait') return
    let cancelled = false
    // 数据到位后统一应用：设置数据 + 首屏自动展开 + 结束 loading
    const applyViewData = (g: GraphData) => {
      setRaw(g)
      setError(null)
      if (view === 'panorama') {
        // 聚合下钻：全部岗位以域超节点常驻；首屏自动展开最大域呈现岗位层
        const agg = aggregateByDomain(g)
        if (agg.supernodes[0]) setExpandedDomains(new Set([agg.supernodes[0].id]))
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

  // 岗位画像下拉选项：进入视图时从 positionCenter 视图取岗位节点（freq 降序）。
  // session 内仅拉取一次（选项为空才请求），再次进入视图不重复请求；
  // 失败保持空选项（下拉无项 + 画布空态），错误态由画像数据请求负责上报
  useEffect(() => {
    if (view !== 'positionPortrait' || portraitOptions.length > 0) return
    let cancelled = false
    apiGet<PanoramaData>('/graph/view/positionCenter?limit=120')
      .then((res) => {
        if (cancelled) return
        const opts = toGraphData(res)
          .nodes.filter((n) => n.type === 'position')
          .map((n) => ({
            id: n.id,
            name: n.name,
            domainId: n.domain_id || 'dom_uncategorized',
            domainName: n.domain_name || '待归类岗位',
          }))
        setPortraitOptions(opts)
        // 默认选中第一个簇（freq 最高的岗位所在簇）+ 该簇最高频岗位；
        // 已有手动选择时不覆盖
        setPortraitCluster((prev) => prev || opts[0]?.domainId || '')
        setPortraitPosition((p) => p || opts[0]?.id || '')
      })
      .catch(() => {
        // 静默：下拉空项即空态提示（见画布渲染处）
      })
    return () => {
      cancelled = true
    }
  }, [view, portraitOptions])

  // 岗位画像数据：按选中岗位请求（缺 position 参数后端 400，故空选时不请求）。
  // 岗位切换频繁，绕过 viewCacheRef 不缓存；portraitPosition / view 变化即重拉
  useEffect(() => {
    if (view !== 'positionPortrait' || !portraitPosition) return
    let cancelled = false
    apiGet<PanoramaData>('/graph/view/positionPortrait', {
      params: { position: portraitPosition, limit: 200 },
    })
      .then((res) => {
        if (cancelled) return
        setRaw(toGraphData(res))
        setError(null)
      })
      .catch((e) => {
        if (!cancelled) {
          setError(
            e instanceof ApiError
              ? { code: e.code, message: e.message }
              : { code: 0, message: '岗位画像加载失败' },
          )
        }
      })
    return () => {
      cancelled = true
    }
  }, [view, portraitPosition])

  // 画像条目（薪资/经验/学历 attr 节点）→ 证据 JD 列表（/graph/position/{id}/
  // portrait-evidence）。大类节点取该维度全部；子条目名 '1-1.3万 ×9' 去 ×N 作 label。
  // loading 由请求 key 派生（state.key ≠ 当前 key 即在加载），避免 effect 体内
  // 同步 setState（react-hooks 级联渲染 lint）
  const [peState, setPeState] = useState<{
    key: string
    data: PortraitEvidenceData | null
    loading: boolean
  }>({ key: '', data: null, loading: false })

  useEffect(() => {
    if (view !== 'positionPortrait' || !selected || selected.type !== 'attr') return
    const dimension = portraitDimension(selected)
    if (!dimension) return
    const key = `${portraitPosition}:${dimension}:${portraitLabel(selected)}`
    let cancelled = false
    apiGet<PortraitEvidenceData>(
      `/graph/position/${encodeURIComponent(portraitPosition)}/portrait-evidence?dimension=${dimension}${portraitLabel(selected) ? `&label=${encodeURIComponent(portraitLabel(selected))}` : ''}`,
    )
      .then((res) => {
        if (!cancelled) setPeState({ key, data: res, loading: false })
      })
      .catch(() => {
        if (!cancelled) setPeState({ key, data: null, loading: false })
      })
    return () => {
      cancelled = true
    }
  }, [selected, view, portraitPosition])

  const peKey =
    view === 'positionPortrait' && selected?.type === 'attr' && portraitDimension(selected)
      ? `${portraitPosition}:${portraitDimension(selected)}:${portraitLabel(selected)}`
      : ''
  const portraitEvidence = peState.key === peKey ? peState.data : null
  const portraitEvidenceLoading = peState.key !== peKey

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
    if (!selected || selected.type !== 'position' || selected.isDomain) return
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

  // skill 详情视图：选中技能节点且详情已就绪（skill_id 匹配）时展示，否则视为加载中。
  // 包 useMemo：条件分支构造的 loading 占位对象若每渲染重建，下游 learningPath/
  // 导学 useMemo 依赖随之失效（第六轮审查 lint 治理）
  const skillDetailView: SkillDetail | null = useMemo<SkillDetail | null>(() => {
    if (selected?.type !== 'skill') return null
    return skillDetail && skillDetail.skill_id === selected.id && !skillDetail.loading
      ? skillDetail
      : { skill_id: '', positions: [], prerequisites: [], courses: [], loading: true }
  }, [selected, skillDetail])

  // ── 双轨制接入（task 1.1/1.3）：选中技能 → 由先修链派生学习路径 DAG + 导学面板增强 ──
  // 数据源为真实先修链（GET /graph/skill/{id}/prerequisites）：目标技能 ← 直接先修（并联），
  // 更深层链条未知故不自造边（宁缺毋滥）。无先修或非技能节点时不启用 DAG。
  const learningPath = useMemo<LearningPathItem[] | undefined>(() => {
    if (!selected || selected.type !== 'skill') return undefined
    const prereqs = skillDetailView?.prerequisites ?? []
    if (prereqs.length === 0) return undefined
    const depthOne = prereqs.filter((p) => p.depth === 1).map((p) => p.name)
    const items: LearningPathItem[] = prereqs.map((p) => ({
      skill: p.name,
      duration_days: 1,
      start_offset: 0,
      prerequisites: [],
      courses: [],
      priority: 'medium',
    }))
    items.push({
      skill: selected.name,
      duration_days: 1,
      start_offset: 0,
      prerequisites: depthOne,
      courses: [],
      priority: 'high',
    })
    return items
  }, [selected, skillDetailView])
  // 图谱页无候选人上下文：已掌握技能集为空（如何开始按"前置未掌握"预警展示）
  const learnedSkills = useMemo(() => new Set<string>(), [])
  // 选中技能的学习状态：有前置 → 未解锁（需先学前置）；无前置 → 下一步（可直接学）
  const skillLearningStatus = useMemo<LearningStatus | undefined>(() => {
    if (!selected || selected.type !== 'skill') return undefined
    return (skillDetailView?.prerequisites ?? []).length > 0 ? 'locked' : 'doing'
  }, [selected, skillDetailView])

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

  // 搜索下拉：点击外部关闭。下拉面板渲染在搜索框容器（searchBoxRef）内部，
  // contains 判定须覆盖整个容器——否则点击结果项的 mousedown 先卸载下拉，
  // click 落空，搜索定位（focusSkill）永远不触发
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      const t = e.target as Node
      if (searchBoxRef.current?.contains(t)) return
      setSearchResults([])
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // 域聚合（panorama 专用）：全部岗位按 domain_id 分组为超节点 + 域间共享技能边
  const domainAgg = useMemo(() => (data ? aggregateByDomain(data) : null), [data])

  // 点击搜索结果 / 相似技能 / 岗位必备技能 → 选中技能节点 + 定位画布
  // panorama 下技能需先展开其所属岗位（取权重最高的关联岗位，即该技能最核心的簇），
  // 再触发 Graph2D.focusNode 居中高亮；技术栈视图技能已全量展示，直接聚焦。
  const focusSkill = useCallback(
    (id: string, name: string) => {
      setSelected({ id, name, type: 'skill' })
      if (view !== 'techStack' && data) {
        const top = data.edges
          .filter((e) => e.source === id || e.target === id)
          .sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0))[0]
        const pid = top ? (top.source === id ? top.target : top.source) : undefined
        if (pid) {
          setExpandedPositions((prev) => {
            if (prev.has(pid)) return prev
            const next = new Set(prev)
            next.add(pid)
            return next
          })
          // panorama 聚合模式：岗位仅在其所属域展开时才上画布（buildDomainView 过滤），
          // 不同步展开域则技能节点不渲染、focusNode 解析不到坐标——搜索/相似技能定位静默失效
          if (view === 'panorama' && domainAgg) {
            const dom = domainAgg.domainOfPosition.get(pid)
            if (dom && !expandedDomains.has(dom)) {
              setExpandedDomains((prev) => {
                if (prev.has(dom)) return prev
                const next = new Set(prev)
                next.add(dom)
                return next
              })
            }
          }
        }
      }
      setFocusRequest((prev) => ({ id, ts: (prev?.ts ?? 0) + 1 }))
    },
    [view, data, domainAgg, expandedDomains],
  )

  // 画布可见数据（08-29 视图收敛后仅三个页签视图）：
  // - panorama：域聚合三级下钻（域超节点 → 展开域 → 展开岗位技能）
  // - techStack（技能为中心）：岗位 Top-30 保底 + 每技能 Top-K 岗位边降噪
  // - 岗位画像：后端已按选中岗位返回完整子图，前端不再裁剪
  // 展开态高亮集合：展开的岗位 ∪ 展开的域超节点（画布共用白边+辉光视觉）
  const expandedUnion = useMemo(
    () => new Set([...expandedPositions, ...expandedDomains]),
    [expandedPositions, expandedDomains],
  )
  const visibleData = useMemo<GraphData | null>(() => {
    if (!data) return null

    // 岗位画像：后端已按选中岗位返回完整子图（中心岗位 + 属性维度 + 技能，limit=200），
    // 前端不再做 Top-30 / Top-N 裁剪（attr 节点非技能，通用裁剪路径会误删）；
    // 未选岗位时返回 null，画布区显示引导空态（见渲染处）
    if (view === 'positionPortrait') return portraitPosition ? data : null

    // panorama：三级下钻——域超节点（全部岗位可见）→ 展开域 → 展开岗位技能。
    // data 非空 ⇒ domainAgg 非空（二者同源 memo），断言仅为类型收窄
    if (view === 'panorama') {
      return buildDomainView(data, domainAgg!, {
        expandedDomains,
        expandedPositions,
        maxSkillsPerPosition: MAX_SKILLS_PER_POSITION,
      })
    }

    // techStack：岗位显示数量限制（Top-30 按关联度降序，2026-08-15 画布容量限制）：
    // 岗位全量 100+，物理上放不下岗位防重叠所需间距
    // （enforceSpread minGap 60 → 每岗位约 1.06 万 px²，画布仅 ~54 万 px² 容量 ~30 岗位），
    // 且全量渲染节点爆炸不可读。低频岗位不显示，可在搜索/详情面板中触达。
    const MAX_POSITIONS = 30
    const keepPositions = new Set<string>(
      data.nodes
        .filter((n) => n.type === 'position')
        .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
        .slice(0, MAX_POSITIONS)
        .map((p) => p.id),
    )
    const nodes = data.nodes.filter((n) => n.type !== 'position' || keepPositions.has(n.id))
    const nodeIds = new Set(nodes.map((n) => n.id))
    // 08-28 技术栈降噪：每技能仅保留权重 Top-K 的岗位边（该视图边为技能→岗位）。
    // 全量 1719 边交叉成毛线团是视觉混乱主因；Top-4 裁至 ~1/4，配合边透明度渐变。
    const TECH_STACK_EDGES_PER_SKILL = 4
    const bySkill = new Map<string, GraphEdge[]>()
    for (const e of data.edges) {
      if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) continue
      const list = bySkill.get(e.source)
      if (list) list.push(e)
      else bySkill.set(e.source, [e])
    }
    const kept = new Set<GraphEdge>()
    for (const list of bySkill.values()) {
      list.sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0))
      for (const e of list.slice(0, TECH_STACK_EDGES_PER_SKILL)) kept.add(e)
    }
    return { ...data, nodes, edges: [...kept] }
  }, [data, view, portraitPosition, expandedPositions, expandedDomains, domainAgg])
  // 技术栈视图标签降噪白名单：仅 Top-30 高频技能在 LOD band 1 常显标签
  // （其余技能放大到 band 2 才显示；非 techStack 视图传 null 走原中位阈值口径）
  const skillLabelTopIds = useMemo(() => {
    if (view !== 'techStack' || !data) return null
    const top = data.nodes
      .filter((n) => n.type === 'skill')
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
      .slice(0, 30)
    return new Set(top.map((n) => n.id))
  }, [view, data])

  // WebGL2 不可用时 3D 按钮禁用，自动保持 2D（设计文档 §6.3 降级策略）
  const webgl2Available = useMemo(() => isWebGL2Available(), [])
  // 触控设备（移动/平板，粗指针）固定 2D 模式（设计文档 §6.3：平板/移动端固定 2D）
  const isCoarsePointer = useMemo(() => window.matchMedia('(pointer: coarse)').matches, [])
  /** 3D 模式是否被锁定（WebGL2 不可用或触控设备） */
  const mode3dLocked = !webgl2Available || isCoarsePointer

  // 选中节点的关联统计（从当前视图数据中实时计算）+ 全图最大关联度（详情条归一化基准）
  const detailStats = useMemo(() => {
    if (!selected || !data) return undefined
    // 岗位画像视图：层级子图里关联统计口径失效（技能/条目挂大类下不直连，
    // positionCount/skillCount/evidenceCount 恒 0 → 侧栏渲染孤立「0」卡）。
    // 改算画像语义：维度大类数 + 画像条目数。
    if (view === 'positionPortrait') {
      const linkedIds = new Set<string>()
      data.edges.forEach((e) => {
        if (e.source === selected.id) linkedIds.add(e.target)
        if (e.target === selected.id) linkedIds.add(e.source)
      })
      const linked = data.nodes.filter((n) => linkedIds.has(n.id))
      const maxValue = Math.max(1, ...data.nodes.map((n) => n.value ?? 0))
      return {
        // 复用三卡位：岗位卡=画像大类数，技能卡=画像条目数，evidence 卡不渲染
        positionCount: linked.filter((n) => n.type === 'attr').length,
        skillCount: linked.filter((n) => n.type !== 'position' && n.type !== 'attr').length,
        maxValue,
      }
    }
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
  }, [selected, data, view])

  // 演示视角书签：锚点以全量数据判定存在（聚合模式下岗位可能在未展开域内）；
  // 点击时若锚点岗位尚未上画布，先展开其所属域，待布局启动后再镜头飞行
  const visibleBookmarks = useMemo(
    () =>
      data
        ? DEMO_BOOKMARKS.filter((b) => data.nodes.some((n) => n.id === b.nodeName || n.name === b.nodeName))
        : [],
    [data],
  )
  const flyToBookmark = useCallback(
    (nodeName: string) => {
      const fly = () => {
        const node = visibleData?.nodes.find((n) => n.id === nodeName || n.name === nodeName)
        if (!node) return
        if (mode === '3d') graph3dRef.current?.flyTo(node.id)
        else graphRef.current?.flyTo(node.id)
      }
      const onCanvas = visibleData?.nodes.some((n) => n.id === nodeName || n.name === nodeName)
      if (onCanvas) {
        fly()
        return
      }
      // 锚点岗位在未展开域内：展开所属域（聚合模式），450ms 后飞行（布局启动）
      if (view === 'panorama' && domainAgg && data) {
        const target = data.nodes.find((n) => n.id === nodeName || n.name === nodeName)
        const dom = target ? domainAgg.domainOfPosition.get(target.id) : undefined
        if (dom && !expandedDomains.has(dom)) toggleDomain(dom)
        window.setTimeout(fly, 450)
      }
    },
    [visibleData, mode, view, domainAgg, data, expandedDomains, toggleDomain],
  )

  // 大屏演示模式（答辩/录屏）：AppShell 按 focusMode 裁掉顶导与侧栏，画布撑满
  // 视口、详情栏转浮层。进入时尝试浏览器全屏（被拒/被浏览器退出均静默降级为页内全屏）
  const focusMode = useUIStore((s) => s.focusMode)
  const toggleFocusMode = useUIStore((s) => s.toggleFocusMode)
  const closeFocusMode = useUIStore((s) => s.closeFocusMode)
  const enterFocus = useCallback(() => {
    toggleFocusMode()
    document.documentElement.requestFullscreen?.().catch(() => {})
  }, [toggleFocusMode])
  const exitFocus = useCallback(() => {
    closeFocusMode()
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
  }, [closeFocusMode])

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
        description="从职能域进入岗位，再沿技能关系定位能力要求与演化信号"
        className="flex-col pb-4 sm:flex-row sm:items-center"
        actions={
          <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:flex-nowrap">
            <div ref={searchBoxRef} className="relative min-w-0 flex-1 sm:w-72 sm:flex-none">
              <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-ink-faint" />
              <Input
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value)
                  if (!event.target.value.trim()) {
                    setSearchResults([])
                    setSearchDone(false)
                  }
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') doSearch(query)
                  if (event.key === 'Escape') {
                    setSearchResults([])
                    setSearchDone(false)
                  }
                }}
                placeholder="搜索技能并定位关系"
                className="h-9 pl-8 pr-16 text-xs"
                role="combobox"
                aria-label="搜索图谱技能"
                aria-expanded={searchResults.length > 0 || (searchDone && !searching && !!query.trim())}
                aria-controls="graph-search-results"
                aria-autocomplete="list"
              />
              <Button size="sm" variant="ghost" className="absolute right-0.5 top-1 h-7 px-2 text-xs" onClick={() => doSearch(query)} disabled={searching}>
                {searching ? <Loader2 className="size-3 animate-spin" /> : '定位'}
              </Button>
              {(searchResults.length > 0 || (searchDone && !searching && query.trim())) && (
                <div id="graph-search-results" role="listbox" className="absolute right-0 top-11 z-40 w-full overflow-hidden rounded-lg border border-border bg-canvas p-1 shadow-lg">
                  {searchResults.length > 0 ? searchResults.map((result) => (
                    <button key={result.id} type="button" role="option" aria-selected="false" onClick={() => focusSkill(result.id, result.name)} className="flex w-full items-center justify-between rounded-md px-2.5 py-2 text-left text-xs hover:bg-subtle">
                      <span className="font-medium text-ink">{result.name}</span>
                      <span className="font-mono text-[12px] text-ink-faint">相关度 {(result.score * 100).toFixed(0)}</span>
                    </button>
                  )) : <p className="px-2.5 py-2 text-xs text-ink-muted">未找到与“{query.trim()}”相关的技能</p>}
                </div>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {/* 原「视图说明」面板唯一不可替代的信息：3D 锁定原因（触控/WebGL2），仅锁定时显示 */}
              {mode3dLocked && (
                <span className="text-[12px] text-ink-faint" data-testid="graph-3d-locked-hint">
                  {isCoarsePointer ? '触控设备固定 2D 模式' : 'WebGL2 不可用，已降级 2D 模式'}
                </span>
              )}
              <div className="flex shrink-0 items-center rounded-lg border border-border bg-subtle/60 p-0.5" aria-label="图谱维度">
                <Button size="sm" variant={mode === '2d' ? 'default' : 'ghost'} onClick={() => setMode('2d')} className="h-8 px-3 text-xs" aria-label="2D">2D 分析</Button>
                <Button size="sm" variant={mode === '3d' ? 'default' : 'ghost'} onClick={() => setMode('3d')} disabled={mode3dLocked} aria-label="3D" title={isCoarsePointer ? '触控设备固定 2D 模式（设计文档 §6.3）' : !webgl2Available ? '当前环境不支持 WebGL2，已降级 2D 模式' : '3D 沉浸式浏览'} className="h-8 px-3 text-xs">3D 浏览</Button>
              </div>
            </div>
          </div>
        }
      />

      <Tabs
        value={view}
        onValueChange={(value) => {
          // 视图切换：同步清空展开的岗位/域（新视图技能集不同）与选中态
          // （P0-1：选中残留会让详情面板指向画布外节点，且 Graph2D 对仍在
          // 数据中的旧节点 dispatch highlight → adjacency blur 把全图压暗到 10%）
          setSelected(null)
          setExpandedPositions(new Set())
          setExpandedDomains(new Set())
          setView(value as GraphTab)
        }}
      >
        <div className="mb-3 flex flex-col gap-3 rounded-xl border border-border bg-subtle/40 px-3 py-3 sm:px-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
            <TabsList className="h-auto w-full justify-start overflow-x-auto bg-canvas p-1 sm:w-auto">
              {(Object.keys(VIEW_LABEL) as GraphTab[]).map((item) => (
                <TabsTrigger key={item} value={item} className="whitespace-nowrap px-3 text-xs" title={VIEW_DESC[item]}>{VIEW_LABEL[item]}</TabsTrigger>
              ))}
            </TabsList>
            <p className="truncate text-[12px] text-ink-muted" title={VIEW_DESC[view]}>{VIEW_DESC[view]}</p>
            {/* 岗位画像：岗位下拉（positionCenter 岗位集，默认选中最高频岗位） */}
            {view === 'positionPortrait' && (() => {
              // 两级下拉：岗位簇（职能域聚合）→ 簇内岗位联动过滤
              const clusterMap = new Map<string, string>()
              for (const o of portraitOptions) clusterMap.set(o.domainId, o.domainName)
              const clusters = [...clusterMap.entries()].map(([id, name]) => ({ id, name }))
              const inCluster = portraitOptions.filter((o) => o.domainId === portraitCluster)
              return (
                <>
                  <Select
                    value={portraitCluster || undefined}
                    onValueChange={(v) => {
                      setSelected(null)
                      setPortraitCluster(v)
                      // 切簇：岗位自动切到该簇内最高频岗位（portraitOptions 已按 freq 降序）
                      const first = portraitOptions.find((o) => o.domainId === v)
                      setPortraitPosition(first?.id || '')
                    }}
                  >
                    <SelectTrigger className="h-8 w-40 shrink-0 text-xs" aria-label="选择岗位簇">
                      <SelectValue placeholder="选择岗位簇…" />
                    </SelectTrigger>
                    <SelectContent>
                      {clusters.map((cl) => (
                        <SelectItem key={cl.id} value={cl.id} className="text-xs">{cl.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Select
                    value={portraitPosition || undefined}
                    onValueChange={(v) => {
                      // 切换岗位：同步清空选中态（详情面板不残留上一岗位的画布外节点）
                      setSelected(null)
                      setPortraitPosition(v)
                    }}
                  >
                    <SelectTrigger className="h-8 w-56 shrink-0 text-xs" aria-label="选择岗位">
                      <SelectValue placeholder="选择岗位…" />
                    </SelectTrigger>
                    <SelectContent>
                      {inCluster.map((o) => (
                        <SelectItem key={o.id} value={o.id} className="text-xs">{o.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </>
              )
            })()}
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-muted" aria-label="当前图谱规模">
            <span className="flex items-center gap-1" title="当前视图节点数 / 图谱总节点数"><Network className="size-3" /><b className="font-mono font-medium text-ink-secondary">{data.stats.returnedNodes}</b><span>/ {data.stats.totalNodesInGraph} 节点</span></span>
            <span className="flex items-center gap-1" title={visibleData && visibleData.edges.length !== data.stats.totalEdges ? `画布渲染 ${visibleData.edges.length} 条（降噪裁剪，全量 ${data.stats.totalEdges}）` : '当前视图边数'}><Box className="size-3" /><b className="font-mono font-medium text-ink-secondary">{visibleData?.edges.length ?? data.stats.totalEdges}</b><span>边</span></span>
            {visibleData && <span><b className="font-mono font-medium text-ink-secondary">{visibleData.nodes.filter((node) => node.type === 'position').length}</b> 岗位 · <b className="font-mono font-medium text-ink-secondary">{visibleData.nodes.filter((node) => node.type === 'skill').length}</b> 技能</span>}
            {data.stats.returnedNodes < data.stats.totalNodesInGraph && <span className="rounded bg-elevated px-1.5 py-0.5">采样视图</span>}
          </div>
        </div>
      </Tabs>

      {/* 画布 + 详情面板：画布占 70-75%，详情占 25-30%。
          大屏演示模式（focusMode）：画布 Card 转为 fixed 全屏（同树仅切类名，
          组件不重挂载、力导向布局不重算），详情栏转为右侧浮层 */}
      <div className={focusMode ? 'relative' : 'grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-4'}>
        <Card className={cn('relative overflow-hidden border-atlas-grid', focusMode ? 'fixed inset-0 z-40' : 'h-[min(720px,calc(100dvh-210px))] min-h-[560px]')}>
          {/* 画布操作组：重置视角 + 大屏演示切换（答辩/录屏用，Esc 退出） */}
          <div className="absolute right-3 top-3 z-20 flex items-center gap-1 rounded-md border border-atlas-grid bg-canvas/90 p-1 shadow-md backdrop-blur-xl">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => (mode === '3d' ? graph3dRef.current?.resetView() : graphRef.current?.resetView())}
              className="h-7 px-2 text-xs text-ink-muted hover:text-ink"
              title="重置视角"
            >
              <RotateCcw className="size-3 mr-1" />
              重置视角
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={focusMode ? exitFocus : enterFocus}
              className="h-7 px-2 text-xs text-ink-muted hover:text-ink"
              title={focusMode ? '退出大屏演示（Esc）' : '大屏演示（隐藏导航，画布占满屏幕）'}
            >
              {focusMode ? <Minimize2 className="size-3 mr-1" /> : <Maximize2 className="size-3 mr-1" />}
              {focusMode ? '退出演示' : '大屏演示'}
            </Button>
          </div>
          {/* 演示视角书签：镜头平滑飞行到锚定岗位簇（仅展示当前视图中存在的锚点；
              右下角避开顶部操作提示与底部视图说明） */}
          {visibleBookmarks.length > 0 && (
            <div className="absolute bottom-12 right-3 z-20 hidden items-center gap-1 rounded-md border border-atlas-grid bg-canvas/90 p-1 shadow-sm backdrop-blur-xl sm:flex" aria-label="演示镜头书签">
              <Crosshair className="mx-1 size-3 text-atlas-muted" />
              {visibleBookmarks.map((bookmark) => (
                <Button
                  key={bookmark.nodeName}
                  size="sm"
                  variant="ghost"
                  onClick={() => flyToBookmark(bookmark.nodeName)}
                  className="h-7 px-2 text-[12px] text-ink-muted hover:text-ink"
                  title={`镜头飞至${bookmark.label}（${bookmark.nodeName}）`}
                >
                  {bookmark.label}
                </Button>
              ))}
            </div>
          )}
          {view === 'positionPortrait' && !portraitPosition ? (
            // 空态：未选岗位时不请求数据（visibleData 为 null），画布区仅显示引导提示
            <div className="flex h-full w-full items-center justify-center text-sm text-ink-muted">
              请选择岗位查看画像
            </div>
          ) : mode === '2d' ? (
            <Graph2D
              ref={graphRef}
              data={visibleData!}
              expandedPositions={expandedUnion}
              selectedId={selected?.id ?? null}
              focusRequest={focusRequest}
              onSelectNode={handleSelectNode}
              onTogglePosition={togglePosition}
              onToggleDomain={toggleDomain}
              learningPath={learningPath}
              completedSkills={[]}
              evolutionMarks={evolutionMarks}
              skillLabelTopIds={skillLabelTopIds}
              ringLayout={view === 'techStack' || view === 'positionPortrait'}
              className="h-full w-full"
            />
          ) : (
            <Suspense fallback={<div className="flex h-full w-full items-center justify-center text-sm text-ink-muted">加载 3D 渲染引擎…</div>}>
              <Graph3D
                ref={graph3dRef}
                data={visibleData!}
                expandedPositions={expandedUnion}
                selectedId={selected?.id ?? null}
                focusRequest={focusRequest}
                onSelectNode={handleSelectNode}
                onTogglePosition={togglePosition}
                onToggleDomain={toggleDomain}
                className="h-full w-full"
              />
            </Suspense>
          )}
          {/* 画布操作提示（可关闭） */}
          {showOperationHint && (
            <div className="absolute top-14 right-2 z-10 max-w-[180px] rounded-md border border-border bg-canvas/90 backdrop-blur px-2.5 py-2 shadow-sm">
              <div className="flex items-start justify-between gap-2">
                <span className="text-[12px] font-medium text-ink-secondary">操作提示</span>
                <button
                  onClick={() => setShowOperationHint(false)}
                  className="rounded-sm text-ink-faint hover:text-ink hover:bg-subtle"
                  aria-label="关闭操作提示"
                >
                  <X className="size-3" />
                </button>
              </div>
              <ul className="mt-1.5 space-y-0.5 text-[12px] text-ink-muted">
                {mode === '3d' ? (
                  <>
                    <li>滚轮缩放 · 拖拽空白旋转视角</li>
                    <li>拖拽节点调整位置</li>
                    <li>单击节点查看详情</li>
                    <li>双击职能域展开/收起岗位</li>
                    <li>双击岗位展开/收起技能</li>
                  </>
                ) : (
                  <>
                    <li>滚轮缩放 · 拖拽空白平移</li>
                    <li>拖拽节点调整位置</li>
                    <li>单击节点查看详情</li>
                    <li>双击职能域展开/收起岗位</li>
                    <li>双击岗位展开/收起技能</li>
                  </>
                )}
              </ul>
            </div>
          )}
        </Card>

        {/* 节点详情面板 + 图谱算法分析：桌面=右侧边栏；移动端=底部抽屉（task T4）。
            大屏演示模式：转为右侧浮层（z 高于全屏画布），选中节点时出现 */}
        {!(focusMode && !selected) && (
          <div className={focusMode ? 'fixed bottom-3 right-3 top-3 z-50 w-[380px]' : 'contents'}>
            <GraphDetailRail
              rightTab={rightTab}
              onRightTabChange={setRightTab}
              ready={!!selected}
              onClose={() => setSelected(null)}
              className={focusMode ? 'h-full shadow-lg' : 'h-[640px]'}
            >
              <TabsContent value="detail" className="flex-1 overflow-y-auto mt-0 px-0 py-0">
                <NodeDetailPanel
                  node={selected}
                  stats={detailStats}
                  skillDetail={skillDetailView}
                  positionDetail={selected?.type === 'position' && positionDetail && positionDetail.id === selected.id ? positionDetail : null}
                  skillEvidence={selected && skillDetail && skillDetail.skill_id === selected.id ? skillEvidence : []}
                  similarSkills={selected && skillDetail && skillDetail.skill_id === selected.id ? similarSkills : []}
                  positionExpanded={
                    selected?.isDomain
                      ? expandedDomains.has(selected.id)
                      : selected?.type === 'position'
                        ? expandedPositions.has(selected.id)
                        : false
                  }
                  onTogglePosition={togglePosition}
                  portraitMode={view === 'positionPortrait'}
                  portraitEvidence={
                    selected?.type === 'attr' && portraitEvidence && portraitEvidence.position_id === portraitPosition
                      ? portraitEvidence
                      : null
                  }
                  portraitEvidenceLoading={portraitEvidenceLoading}
                  onToggleDomain={toggleDomain}
                  onSelectSkill={focusSkill}
                  onClose={() => setSelected(null)}
                  learningStatus={skillLearningStatus}
                  learnedSkills={learnedSkills}
                />
              </TabsContent>
              <TabsContent value="analysis" className="flex-1 overflow-y-auto mt-0 px-0 py-0">
                <GraphAnalysisPanel
                  skills={data.nodes.filter((n) => n.type === 'skill').map((n) => ({ id: n.id, name: n.name }))}
                  onFocusSkill={focusSkill}
                />
                <GraphCommunityTree className="mt-3" />
              </TabsContent>
            </GraphDetailRail>
          </div>
        )}
      </div>

      {/* 演化时间轴（P0-2）：版本快照滑轨 + 增删打标（接口失败静默隐藏） */}
      <EvolutionTimeline onMarksChange={setEvolutionMarks} className="mt-3" />

      <div className="mt-4 grid gap-3 rounded-lg border border-atlas-grid bg-subtle/60 p-3 text-xs text-ink-muted lg:grid-cols-2 xl:grid-cols-4" role="list" aria-label="图谱图例">
        <div className="space-y-2" role="listitem">
          <p className="font-mono text-[12px] tracking-[0.15em] text-atlas-muted">MAP FEATURES / 实体</p>
          <div className="flex flex-wrap gap-x-3 gap-y-1.5">
            <span className="flex items-center gap-1.5"><span className="size-3 rotate-45" style={{ background: 'conic-gradient(from 45deg, #8b8af8, #38bdf8, #34d399, #fbbf24, #f472b6, #8b8af8)' }} role="img" aria-label="职能域节点：按域社区着色（每域一色）" /> 职能域<span className="text-ink-faint">（每域一色）</span></span>
            <span className="flex items-center gap-1.5"><span className="h-2.5 w-3.5 rounded-sm border border-atlas-ocean bg-state-stable" /> 岗位</span>
            <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-full bg-graph-skill" role="img" aria-label="技术技能节点" /> 技术技能</span>
            <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-full border-2 border-graph-soft-skill" role="img" aria-label="软技能节点：粉色空心圆" /> 软技能</span>
            <span className="flex items-center gap-1.5"><span className="size-0 border-x-4 border-b-[7px] border-x-transparent border-b-graph-evidence" /> 证据地标</span>
            <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-full bg-[#22c55e]" role="img" aria-label="演化时间轴：本版新增节点绿环" /> 本版新增<span className="text-ink-faint">（时间轴）</span></span>
            <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-full border-2 border-dashed border-state-declining" role="img" aria-label="演化时间轴：本版消亡节点橙色虚线圈" /> 本版消亡<span className="text-ink-faint">（时间轴）</span></span>
          </div>
        </div>
        <div className="space-y-2" role="listitem">
          <p className="font-mono text-[12px] tracking-[0.15em] text-atlas-muted">RELATION SURVEY / 关系</p>
          <div className="flex flex-wrap gap-x-3 gap-y-1.5">
            <span className="flex items-center gap-1.5"><span className="h-0.5 w-5 bg-atlas-ocean" /> 必备关系</span>
            <span className="flex items-center gap-1.5"><span className="w-5 border-t border-dashed border-atlas-muted" /> 加分关系</span>
            <span className="flex items-center gap-1.5"><span className="w-5 border-t border-dotted border-atlas-muted" /> 共享能力关联</span>
          </div>
        </div>
        <div className="space-y-2" role="listitem">
          <p className="font-mono text-[12px] tracking-[0.15em] text-atlas-muted">SKILL CATEGORY / 技能类目</p>
          <div className="flex flex-wrap gap-x-2.5 gap-y-1.5">
            {SKILL_CATEGORY_PALETTE.map((cat) => (
              <span key={cat.label} className="flex items-center gap-1">
                <span className="size-2 rounded-full" style={{ backgroundColor: cat.color }} />
                {cat.label}
              </span>
            ))}
          </div>
        </div>
        <div className="space-y-2" role="listitem">
          <p className="font-mono text-[12px] tracking-[0.15em] text-atlas-muted">POSITION STATUS / 岗位状态色</p>
          <div className="flex flex-wrap gap-x-2.5 gap-y-1.5">
            {(['active', 'stable', 'emerging', 'candidate', 'declining', 'archived'] as PositionState[]).map((s) => (
              <span key={s} className="flex items-center gap-1">
                <span className={`size-2 rounded-full ${POSITION_STATE_DOT[s]}`} />
                {POSITION_STATE_META[s].label}
              </span>
            ))}
          </div>
        </div>      </div>
    </>
  )
}
