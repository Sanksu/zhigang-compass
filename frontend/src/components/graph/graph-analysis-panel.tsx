/**
 * 图谱算法分析面板 — 设计文档 §7.1 图算法应用
 *
 * 三块：技能重要性（PageRank）/ 技能簇（Louvain）/ 技能最短路径
 * - PageRank 排行：GET /graph/algorithms/pagerank，Top-N 技能重要性排序
 * - 技能簇：GET /graph/algorithms/skill-clusters，Louvain 技术栈聚类
 * - 最短路径：GET /graph/algorithms/shortest-path，两技能可达路径
 * 点击技能定位画布（onFocusSkill 回调，复用现有 focusRequest 机制）
 */
import { useEffect, useState } from 'react'
import { BarChart2, Boxes, GitBranch, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { apiGet } from '@/lib/api'

interface PagerankSkill {
  id: string
  name: string
  score: number
}

interface ClusterSkill {
  id: string
  name: string
}

/** LLM 兜底命名结果（后端 skill-clusters 响应，契约 openapi.yaml 已声明） */
interface ClusterLLM {
  coherent: boolean
  cluster_name: string | null
  rationale: string | null
  splits: string[]
}

interface SkillCluster {
  id: number
  size: number
  /** 规则标签（簇内共现权重 Top-3 技能，· 拼接） */
  label?: string
  /** 是否命中 LLM 兜底触发条件（无主导技能/跨类别复合栈/规则标签为空） */
  needs_llm?: boolean
  /** 触发原因（no_dominant_skill / cross_category / empty_label） */
  triggers?: string[]
  /** LLM 兜底结果（未触发或 LLM 降级时为 null） */
  llm?: ClusterLLM | null
  skills: ClusterSkill[]
}

/** 层级元数据（阶段三层次化提取：0 = 最细，逐层变粗） */
interface ClusterLevel {
  level: number
  cluster_count: number
  modularity: number
}

interface PathNode {
  id: string
  name: string
  type: string
}

interface GraphAnalysisPanelProps {
  /** 当前画布技能 id → name 映射，用于最短路径选择下拉 */
  skills: { id: string; name: string }[]
  /** 点击技能定位画布 */
  onFocusSkill: (id: string, name: string) => void
  className?: string
}

const PATH_TYPE_LABEL: Record<string, string> = {
  Skill: '技能',
  Position: '岗位',
  Evidence: '证据',
}

/** 技能簇标题：LLM 语义命名优先 → 规则标签 → 簇内首技能 → 兜底 */
function clusterTitle(c: SkillCluster): string {
  return c.llm?.cluster_name || c.label || c.skills[0]?.name || `簇${c.id}`
}

/** 簇列表默认展示条数（超出显示「展开更多」） */
const CLUSTER_PREVIEW_COUNT = 12

export function GraphAnalysisPanel({ skills, onFocusSkill, className }: GraphAnalysisPanelProps) {
  const [pagerank, setPagerank] = useState<PagerankSkill[] | null>(null)
  const [pagerankLoading, setPagerankLoading] = useState(true)
  const [clusters, setClusters] = useState<SkillCluster[] | null>(null)
  const [clusterLoading, setClusterLoading] = useState(true)
  const [expandedCluster, setExpandedCluster] = useState<number | null>(null)
  const [showAllClusters, setShowAllClusters] = useState(false)
  // 阶段三：层级元数据 + 当前选中层级（null = 最优层）
  const [levels, setLevels] = useState<ClusterLevel[] | null>(null)
  const [selectedLevel, setSelectedLevel] = useState<number | null>(null)

  // 最短路径状态
  const [fromSkill, setFromSkill] = useState('')
  const [toSkill, setToSkill] = useState('')
  const [path, setPath] = useState<PathNode[] | null>(null)
  const [pathLoading, setPathLoading] = useState(false)
  const [pathError, setPathError] = useState<string | null>(null)

  // 加载 PageRank（30s TTL 缓存，懒加载一次）
  // 注意：pagerankLoading 初始 true，无需在 effect 内再次 set，避免 react-hooks/set-state-in-effect
  useEffect(() => {
    let cancelled = false
    apiGet<{ skills: PagerankSkill[] }>('/graph/algorithms/pagerank?top_n=20')
      .then((r) => {
        if (!cancelled) setPagerank(r.skills)
      })
      .catch(() => {
        /* 算法端点不可用时面板降级为空态，不阻塞图谱主功能 */
      })
      .finally(() => {
        if (!cancelled) setPagerankLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 加载技能簇（随选中层级变化重新请求；null = 最优层）
  // 注意：clusterLoading 初始 true，无需在 effect 内再次 set，避免 react-hooks/set-state-in-effect
  useEffect(() => {
    let cancelled = false
    const levelQuery = selectedLevel === null ? '' : `&level=${selectedLevel}`
    ;(async () => {
      try {
        const r = await apiGet<{ clusters: SkillCluster[]; levels: ClusterLevel[] | null }>(
          `/graph/algorithms/skill-clusters?min_size=2${levelQuery}`,
        )
        if (!cancelled) {
          setClusters(r.clusters)
          if (r.levels && r.levels.length > 0) setLevels(r.levels)
        }
      } catch {
        /* 算法端点不可用时面板降级为空态，不阻塞图谱主功能 */
      } finally {
        if (!cancelled) setClusterLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [selectedLevel])

  /** 切换 dendrogram 层级（null = 最优层） */
  function changeLevel(value: string) {
    setSelectedLevel(value === '' ? null : Number(value))
    setClusterLoading(true)
    setExpandedCluster(null)
    setShowAllClusters(false)
  }

  // 查询最短路径
  async function runShortestPath() {
    if (!fromSkill || !toSkill) {
      setPathError('请选择起止技能')
      return
    }
    setPathLoading(true)
    setPathError(null)
    setPath(null)
    try {
      const r = await apiGet<{ from: string; to: string; path: PathNode[] }>(
        `/graph/algorithms/shortest-path?from=${encodeURIComponent(fromSkill)}&to=${encodeURIComponent(toSkill)}`,
      )
      setPath(r.path)
    } catch {
      setPathError('两技能间不存在 ≤6 跳的可达路径')
    } finally {
      setPathLoading(false)
    }
  }

  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <BarChart2 className="size-4 text-ink-faint" />
          图谱算法分析
        </CardTitle>
        <CardDescription className="text-[11px]">技能重要性 · 技能簇 · 最短路径（设计文档 §7.1）</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* ── PageRank 技能重要性 ── */}
        <section>
          <h4 className="text-xs font-medium text-ink mb-1.5 flex items-center gap-1.5">
            <GitBranch className="size-3 text-ink-faint" />
            技能重要性 Top-20
          </h4>
          {pagerankLoading ? (
            <div className="flex items-center gap-2 py-3 text-xs text-ink-muted">
              <Loader2 className="size-3 animate-spin" />
              加载中…
            </div>
          ) : !pagerank || pagerank.length === 0 ? (
            <p className="py-2 text-xs text-ink-faint">暂无 PageRank 数据</p>
          ) : (
            <ol className="space-y-1 max-h-48 overflow-y-auto pr-1">
              {pagerank.map((s, i) => (
                <li key={s.id}>
                  <button
                    onClick={() => onFocusSkill(s.id, s.name)}
                    className="group flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs transition-colors hover:bg-subtle"
                  >
                    <span className="w-5 text-right font-mono text-[10px] text-ink-faint">{i + 1}</span>
                    <span className="flex-1 truncate font-medium text-ink group-hover:text-ink">{s.name}</span>
                    <span className="font-mono text-[10px] text-ink-faint">{s.score.toFixed(3)}</span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </section>

        {/* ── Louvain 技能簇 ── */}
        <section>
          <h4 className="text-xs font-medium text-ink mb-1.5 flex items-center gap-1.5">
            <Boxes className="size-3 text-ink-faint" />
            技能簇（技术栈聚类）
          </h4>
          {levels && levels.length > 1 && (
            <div className="mb-1.5">
              <Label className="text-[10px] text-ink-faint">层级（dendrogram 粗→细）</Label>
              <select
                value={selectedLevel ?? ''}
                onChange={(e) => changeLevel(e.target.value)}
                className="w-full h-7 rounded border border-border bg-canvas px-2 text-xs outline-none focus:border-ink"
              >
                <option value="">最优层</option>
                {levels.map((l) => (
                  <option key={l.level} value={l.level}>
                    L{l.level} · {l.cluster_count} 簇 · Q={l.modularity.toFixed(3)}
                  </option>
                ))}
              </select>
            </div>
          )}
          {clusterLoading ? (
            <div className="flex items-center gap-2 py-3 text-xs text-ink-muted">
              <Loader2 className="size-3 animate-spin" />
              聚类中…
            </div>
          ) : clusters && clusters.length > 0 ? (
            <div className="space-y-1 max-h-48 overflow-y-auto pr-1">
              {(showAllClusters ? clusters : clusters.slice(0, CLUSTER_PREVIEW_COUNT)).map((c) => (
                <div key={c.id} className="rounded border border-border overflow-hidden">
                  <button
                    onClick={() => setExpandedCluster(expandedCluster === c.id ? null : c.id)}
                    className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left text-xs hover:bg-subtle"
                  >
                    <span className="truncate font-medium text-ink">{clusterTitle(c)}</span>
                    <span className="flex items-center gap-1.5 shrink-0">
                      {c.needs_llm && (
                        <span
                          className="rounded bg-ink/10 px-1 py-0.5 text-[9px] font-medium text-ink-secondary"
                          title={`LLM 兜底命名（触发：${c.triggers?.join('、') ?? '未知'}）`}
                        >
                          LLM
                        </span>
                      )}
                      <span className="rounded bg-subtle px-1 py-0.5 font-mono text-[10px] text-ink-muted">{c.size}</span>
                      <span className="text-ink-faint">▾</span>
                    </span>
                  </button>
                  {expandedCluster === c.id && (
                    <div className="px-2 pb-2">
                      <div className="flex flex-wrap gap-1">
                        {c.skills.map((s) => (
                          <button
                            key={s.id}
                            onClick={() => onFocusSkill(s.id, s.name)}
                            className="rounded bg-subtle px-1.5 py-0.5 text-[10px] text-ink-secondary hover:bg-ink/10"
                          >
                            {s.name}
                          </button>
                        ))}
                      </div>
                      {c.llm?.rationale && (
                        <p className="mt-1.5 text-[10px] text-ink-faint italic">{c.llm.rationale}</p>
                      )}
                      {c.llm?.splits && c.llm.splits.length > 0 && (
                        <p className="mt-1 text-[10px] text-ink-faint">
                          建议拆分：{c.llm.splits.join('、')}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              ))}
              {!showAllClusters && clusters.length > CLUSTER_PREVIEW_COUNT && (
                <button
                  onClick={() => setShowAllClusters(true)}
                  className="w-full rounded border border-border px-2 py-1 text-[10px] text-ink-muted hover:bg-subtle"
                >
                  展开更多（{clusters.length - CLUSTER_PREVIEW_COUNT}）
                </button>
              )}
            </div>
          ) : (
            <p className="py-2 text-xs text-ink-faint">暂无技能簇数据</p>
          )}
        </section>

        {/* ── 最短路径 ── */}
        <section>
          <h4 className="text-xs font-medium text-ink mb-1.5">技能最短路径</h4>
          <div className="space-y-1.5">
            <div>
              <Label className="text-[10px] text-ink-faint">起点技能</Label>
              <select
                value={fromSkill}
                onChange={(e) => {
                  setFromSkill(e.target.value)
                  setPath(null)
                  setPathError(null)
                }}
                className="w-full h-7 rounded border border-border bg-canvas px-2 text-xs outline-none focus:border-ink"
              >
                <option value="">选择技能…</option>
                {skills.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </div>
            <div>
              <Label className="text-[10px] text-ink-faint">终点技能</Label>
              <select
                value={toSkill}
                onChange={(e) => {
                  setToSkill(e.target.value)
                  setPath(null)
                  setPathError(null)
                }}
                className="w-full h-7 rounded border border-border bg-canvas px-2 text-xs outline-none focus:border-ink"
              >
                <option value="">选择技能…</option>
                {skills.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </div>
            <Button size="sm" className="w-full h-7 text-xs" onClick={runShortestPath} disabled={pathLoading}>
              {pathLoading ? '计算中…' : '查询路径'}
            </Button>
            {pathError && <p className="text-[11px] text-state-archived">{pathError}</p>}
            {path && (
              <div className="rounded border border-border p-1.5">
                <p className="text-[10px] text-ink-faint mb-1">可达路径（{path.length} 个节点）：</p>
                <div className="flex flex-wrap items-center gap-1">
                  {path.map((n, i) => (
                    <span key={i} className="flex items-center gap-1">
                      {i > 0 && <span className="text-ink-faint text-[10px]">→</span>}
                      <span
                        className={`rounded px-1 py-0.5 text-[10px] ${
                          n.type === 'Skill' ? 'bg-ink text-canvas' : 'bg-subtle text-ink-secondary'
                        }`}
                        title={PATH_TYPE_LABEL[n.type] ?? n.type}
                      >
                        {n.name}
                      </span>
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>
      </CardContent>
    </Card>
  )
}
