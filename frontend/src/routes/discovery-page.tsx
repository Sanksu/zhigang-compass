/**
 * 新岗位发现页 — 设计文档 §7 动态演化与新岗位发现（08-27）
 *
 * 数据来源：真实后端 API
 * - GET /api/v1/discovery/recent → 近期新岗位 + 图谱技能（近 30 天候选）
 * - GET /api/v1/discovery/position-skills-delta?position=... → 岗位技能增减（最近两版）
 */
import { useEffect, useMemo, useState } from 'react'
import { Sparkles, Radar, ArrowUpRight, ArrowDownRight, Minus, AlertCircle, RotateCcw } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { PositionStateBadge } from '@/components/shared/position-state-badge'
import { cn } from '@/lib/utils'
import { apiGet } from '@/lib/api'
import {
  type DiscoveryRecentData,
  type PositionSkillsDeltaData,
  type RecentDiscoveryCandidate,
} from '@/components/discovery/types'
import { MetricCard } from '@/components/evolution/shared'

type Tab = 'new' | 'delta'

function stateBadge(state: string) {
  return <PositionStateBadge state={state} className="text-[10px]" />
}

function SkillChips({ skill, tone }: { skill: { skill_name: string }[]; tone: 'must' | 'nice' | 'soft' }) {
  if (!skill?.length) return null
  const cls =
    tone === 'must'
      ? 'bg-ink text-canvas'
      : tone === 'nice'
        ? 'bg-subtle text-ink-secondary'
        : 'bg-[#ec4899]/10 text-[#ec4899]'
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {skill.map((s, i) => (
        <span key={`${tone}-${s.skill_name}-${i}`} className={cn('rounded px-1.5 py-0.5 text-[10px]', cls)}>
          {s.skill_name}
        </span>
      ))}
    </div>
  )
}

/** 近期新岗位列表（含技能 chips；candidate 态标注待审核） */
function NewPositionsView({ data, loading }: { data: DiscoveryRecentData | null; loading: boolean }) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())

  if (loading) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-xs text-ink-muted">加载近期新岗位…</CardContent>
      </Card>
    )
  }
  if (!data || data.candidates.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-xs text-ink-faint">
        <AlertCircle className="mx-auto size-5 text-ink-faint mb-2" />
          近 30 天无新岗位候选
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-2">
      {data.candidates.map((c) => {
        const isOpen = expanded.has(c.position_name)
        const mustCount = c.skills?.must?.length ?? 0
        const niceCount = c.skills?.nice?.length ?? 0
        return (
          <Card key={c.position_name}>
            <CardContent className="py-3">
              <button
                type="button"
                className="w-full text-left"
                onClick={() =>
                  setExpanded((prev) => {
                    const n = new Set(prev)
                    if (n.has(c.position_name)) n.delete(c.position_name)
                    else n.add(c.position_name)
                    return n
                  })
                }
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-ink">{c.position_name}</span>
                  {stateBadge(c.state)}
                  <span className="text-[10px] text-ink-faint font-mono ml-auto">
                    发现于 {new Date(c.detected_at).toLocaleDateString('zh-CN')}
                  </span>
                </div>
                {c.skill_pending ? (
                  <p className="text-xs text-ink-muted mt-1">
                    <span className="text-state-candidate">技能待聚合/待审核</span>
                    —— 该岗位发现早于图谱聚合，暂无技能清单
                  </p>
                ) : (
                  <div className="mt-1 text-xs text-ink-muted">
                    <span className="text-ink">必备 {mustCount}</span> · 加分 {niceCount}
                  </div>
                )}
              </button>
              {isOpen && (
                <div className="mt-2 border-t border-border pt-2">
                  {c.skill_pending || !c.skills ? (
                    <p className="text-xs text-ink-faint">暂无技能明细</p>
                  ) : (
                    <>
                      <div className="text-[10px] text-ink-faint">必备技能</div>
                      <SkillChips skill={c.skills.must ?? []} tone="must" />
                      <div className="text-[10px] text-ink-faint mt-2">加分技能</div>
                      <SkillChips skill={c.skills.nice ?? []} tone="nice" />
                      <div className="text-[10px] text-ink-faint mt-2">软技能</div>
                      <SkillChips skill={c.skills.soft ?? []} tone="soft" />
                    </>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}

/** 岗位技能增减视图（选择岗位 → 最近两版 diff） */
function SkillsDeltaView({
  candidates,
  loading,
}: {
  candidates: RecentDiscoveryCandidate[]
  loading: boolean
}) {
  const [selected, setSelected] = useState('')
  const [delta, setDelta] = useState<PositionSkillsDeltaData | null>(null)
  const [deltaError, setDeltaError] = useState<string | null>(null)
  const [deltaLoading, setDeltaLoading] = useState(false)

  // 候选 with 图谱技能（position_id 非空）可选作 diff；无则回退到全部候选名
  const options = useMemo(() => {
    const named = candidates.filter((c) => c.position_id).map((c) => c.position_name)
    return named.length ? named : candidates.map((c) => c.position_name)
  }, [candidates])

  useEffect(() => {
    if (!selected) return
    let cancelled = false
    apiGet<PositionSkillsDeltaData>(`/discovery/position-skills-delta`, {
      params: { position: selected },
    })
      .then((r) => {
        if (!cancelled) {
          setDelta(r)
          setDeltaError(null)
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setDelta(null)
          setDeltaError(e?.message || '技能增减加载失败')
        }
      })
      .finally(() => {
        if (!cancelled) setDeltaLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selected])

  // 选择变化时请求在途 → 复位 loading（置于回调而非 effect 主体，规避 set-state-in-effect）
  const handleSelect = (name: string) => {
    setSelected(name)
    setDelta(null)
    setDeltaError(null)
    if (name) setDeltaLoading(true)
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">岗位技能增减（最近两版快照对比）</CardTitle>
        <CardDescription>
          选择岗位后对比最近两个图谱版本快照，看该岗位新增 / 移除 / 未变技能
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <p className="text-xs text-ink-muted text-center py-6">加载岗位…</p>
        ) : options.length === 0 ? (
          <p className="text-xs text-ink-faint text-center py-6">暂无可用岗位</p>
        ) : (
          <>
            <div className="flex flex-wrap gap-2 mb-4">
              <select
                value={selected}
                onChange={(e) => handleSelect(e.target.value)}
                className="h-8 rounded-md border border-border bg-background px-2 text-xs"
              >
                <option value="">选择岗位…</option>
                {options.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              {delta && (
                <span className="text-[10px] text-ink-faint self-center">
                  {delta.from_version} → {delta.to_version}
                </span>
              )}
            </div>

            {deltaLoading && (
              <p className="text-xs text-ink-muted text-center py-6">加载技能增减…</p>
            )}
            {deltaError && !deltaLoading && (
              <p className="text-xs text-state-archived text-center py-6">{deltaError}</p>
            )}
            {delta && !deltaLoading && (
              <div className="space-y-3">
                <div>
                  <div className="flex items-center gap-1 text-xs text-state-stable font-medium mb-1">
                    <ArrowUpRight className="size-3.5" />新增（{delta.added.length}）
                  </div>
                  <SkillChips skill={delta.added} tone="must" />
                  {delta.added.length === 0 && (
                    <span className="text-[10px] text-ink-faint">无新增</span>
                  )}
                </div>
                <div>
                  <div className="flex items-center gap-1 text-xs text-state-declining font-medium mb-1">
                    <ArrowDownRight className="size-3.5" />移除（{delta.removed.length}）
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {delta.removed.map((s, i) => (
                      <span key={`${s.skill_id}-${i}`} className="rounded bg-state-archived/10 px-1.5 py-0.5 text-[10px] text-state-archived line-through">
                        {s.skill_name}
                      </span>
                    ))}
                    {delta.removed.length === 0 && (
                      <span className="text-[10px] text-ink-faint">无移除</span>
                    )}
                  </div>
                </div>
                <div>
                  <div className="flex items-center gap-1 text-xs text-ink-muted font-medium mb-1">
                    <Minus className="size-3.5" />未变（{delta.unchanged.length}）
                  </div>
                  <SkillChips skill={delta.unchanged} tone="nice" />
                </div>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

export function DiscoveryPage() {
  const [tab, setTab] = useState<Tab>('new')
  const [recent, setRecent] = useState<DiscoveryRecentData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    apiGet<DiscoveryRecentData>('/discovery/recent', { params: { days: 30, limit: 50 } })
      .then((r) => {
        if (!cancelled) {
          setRecent(r)
          setError(null)
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e?.message || '加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  const candidates = recent?.candidates ?? []
  const total = recent?.total ?? 0
  const pendingCount = candidates.filter((c) => c.state === 'candidate').length
  const withSkillsCount = candidates.filter((c) => !c.skill_pending).length

  return (
    <>
      <PageHeader
        title="新岗位发现"
        description="近期发现的新岗位及其技能 · 旧岗位技能增减变化"
        actions={
          <Button variant="outline" size="sm" onClick={() => { setLoading(true); setReloadKey((k) => k + 1) }} disabled={loading}>
            <RotateCcw className="size-3.5" />
            刷新
          </Button>
        }
      />

      {/* 顶部指标卡 */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
        <MetricCard metric={{ key: 'total', label: '近 30 天候选', value: total, delta: 0, tone: 'stable', hint: '进入发现候选池' }} />
        <MetricCard metric={{ key: 'skills', label: '已聚合成图', value: withSkillsCount, delta: 0, tone: 'stable', hint: '有图谱技能清单' }} />
        <MetricCard metric={{ key: 'pending', label: '待审核', value: pendingCount, delta: 0, tone: 'declining', hint: 'candidate 态，技能待聚合' }} />
      </div>

      {/* Tab 切换 */}
      <div className="flex gap-2 mb-4">
        <Button
          size="sm"
          variant={tab === 'new' ? 'default' : 'outline'}
          onClick={() => setTab('new')}
        >
          <Radar className="size-3.5 mr-1" />近期新岗位
        </Button>
        <Button
          size="sm"
          variant={tab === 'delta' ? 'default' : 'outline'}
          onClick={() => setTab('delta')}
        >
          <Sparkles className="size-3.5 mr-1" />技能增减
        </Button>
      </div>

      {error && (
        <Card className="mb-4">
          <CardContent className="py-3 text-xs text-state-archived">{error}</CardContent>
        </Card>
      )}

      {tab === 'new' ? (
        <NewPositionsView data={recent} loading={loading} />
      ) : (
        <SkillsDeltaView candidates={candidates} loading={loading} />
      )}
    </>
  )
}
