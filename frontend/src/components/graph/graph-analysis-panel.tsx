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

interface SkillCluster {
  id: number
  size: number
  skills: ClusterSkill[]
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

export function GraphAnalysisPanel({ skills, onFocusSkill, className }: GraphAnalysisPanelProps) {
  const [pagerank, setPagerank] = useState<PagerankSkill[] | null>(null)
  const [clusters, setClusters] = useState<SkillCluster[] | null>(null)
  const [clusterLoading, setClusterLoading] = useState(false)
  const [expandedCluster, setExpandedCluster] = useState<number | null>(null)

  // 最短路径状态
  const [fromSkill, setFromSkill] = useState('')
  const [toSkill, setToSkill] = useState('')
  const [path, setPath] = useState<PathNode[] | null>(null)
  const [pathLoading, setPathLoading] = useState(false)
  const [pathError, setPathError] = useState<string | null>(null)

  // 加载 PageRank（30s TTL 缓存，懒加载一次）
  useEffect(() => {
    let cancelled = false
    apiGet<{ skills: PagerankSkill[] }>('/graph/algorithms/pagerank?top_n=20')
      .then((r) => {
        if (!cancelled) setPagerank(r.skills)
      })
      .catch(() => {
        /* 算法端点不可用时面板降级为空态，不阻塞图谱主功能 */
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 加载技能簇（懒加载一次）
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const r = await apiGet<{ clusters: SkillCluster[] }>('/graph/algorithms/skill-clusters?min_size=2')
        if (!cancelled) {
          setClusters(r.clusters)
          setClusterLoading(false)
        }
      } catch {
        if (!cancelled) {
          setClusters([])
          setClusterLoading(false)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

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
          {pagerank === null ? (
            <div className="flex items-center gap-2 py-3 text-xs text-ink-muted">
              <Loader2 className="size-3 animate-spin" />
              加载中…
            </div>
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
          {clusterLoading && clusters === null ? (
            <div className="flex items-center gap-2 py-3 text-xs text-ink-muted">
              <Loader2 className="size-3 animate-spin" />
              聚类中…
            </div>
          ) : clusters && clusters.length > 0 ? (
            <div className="space-y-1 max-h-48 overflow-y-auto pr-1">
              {clusters.slice(0, 12).map((c) => (
                <div key={c.id} className="rounded border border-border overflow-hidden">
                  <button
                    onClick={() => setExpandedCluster(expandedCluster === c.id ? null : c.id)}
                    className="flex w-full items-center justify-between px-2 py-1.5 text-left text-xs hover:bg-subtle"
                  >
                    <span className="truncate font-medium text-ink">{c.skills[0]?.name ?? `簇${c.id}`}</span>
                    <span className="flex items-center gap-1.5">
                      <span className="rounded bg-subtle px-1 py-0.5 font-mono text-[10px] text-ink-muted">{c.size}</span>
                      <span className="text-ink-faint">▾</span>
                    </span>
                  </button>
                  {expandedCluster === c.id && (
                    <div className="flex flex-wrap gap-1 px-2 pb-2">
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
                  )}
                </div>
              ))}
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
