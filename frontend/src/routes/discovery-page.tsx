/**
 * 新岗位发现页 — 设计文档 §7 动态演化与新岗位发现（08-27）
 *
 * 数据来源：真实后端 API
 * - GET /api/v1/discovery/recent → 近期新岗位 + 图谱技能（近 30 天候选）
 * - GET /api/v1/discovery/position-skills-delta?position=... → 岗位技能增减（最近两版）
 */
import { useEffect, useMemo, useState } from 'react'
import { Sparkles, Radar, ArrowUpRight, ArrowDownRight, Minus, AlertCircle } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { PositionStateBadge } from '@/components/shared/position-state-badge'
import { SkillChip, type SkillChipTone } from '@/components/shared/skill-chips'
import { RefreshButton } from '@/components/shared/refresh-button'
import { apiGet } from '@/lib/api'
import {
  type DiscoveryRecentData,
  type PositionSkillsDeltaData,
  type RecentDiscoveryCandidate,
} from '@/components/discovery/types'
import { MetricCard } from '@/components/shared/metric-card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

type Tab = 'new' | 'delta'

/** 未变技能默认展示数（超过则折叠，展开按钮显示全量） */
const UNCHANGED_PREVIEW = 12

function stateBadge(state: string) {
  return <PositionStateBadge state={state} className="text-[10px]" />
}

function SkillChips({ skill, tone }: { skill: { skill_name: string }[]; tone: SkillChipTone }) {
  if (!skill?.length) return null
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {skill.map((s) => (
        <SkillChip key={`${tone}-${s.skill_name}`} tone={tone}>
          {s.skill_name}
        </SkillChip>
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
  // 未变技能折叠：聚合岗位技能可达上百个，全量渲染会把新增/移除挤出首屏
  const [unchangedExpanded, setUnchangedExpanded] = useState(false)

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
    setUnchangedExpanded(false)
    if (name) setDeltaLoading(true)
  }

  // 后端按 skill_id 排序（对用户无意义），展示前按技能名排序
  const bySkillName = (a: { skill_name: string }, b: { skill_name: string }) =>
    a.skill_name.localeCompare(b.skill_name, 'zh-Hans-CN')

  // 快照日期（版本 id 无阅读意义，放 title 溯源）
  const fmtSnapDate = (iso: string | null | undefined) =>
    iso ? new Date(iso).toLocaleDateString('zh-CN') : '?'

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
              <Select
                value={selected || 'none'}
                onValueChange={(v) => handleSelect(v === 'none' ? '' : v)}
              >
                <SelectTrigger className="h-8 w-56 text-xs">
                  <SelectValue placeholder="选择岗位…" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">选择岗位…</SelectItem>
                  {options.map((name) => (
                    <SelectItem key={name} value={name}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {delta && (
                <span
                  className="text-[10px] text-ink-faint self-center"
                  title={`${delta.from_version ?? '?'} → ${delta.to_version ?? '?'}`}
                >
                  {fmtSnapDate(delta.from_created_at)} → {fmtSnapDate(delta.to_created_at)} 快照
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
              delta.added.length + delta.removed.length + delta.unchanged.length === 0 ? (
                <p className="text-xs text-ink-faint text-center py-6">
                  该岗位最近两版快照间无技能数据
                </p>
              ) : (
                <div className="space-y-3">
                  {/* 全新岗位解读：本期首次入图，无未变/移除基线 */}
                  {delta.added.length > 0 && delta.removed.length === 0 && delta.unchanged.length === 0 && (
                    <p className="text-[10px] text-ink-faint">
                      该岗位为本期新入图谱，全部技能计为新增
                    </p>
                  )}
                  <div>
                    <div className="flex items-center gap-1 text-xs text-state-stable font-medium mb-1">
                      <ArrowUpRight className="size-3.5" />新增（{delta.added.length}）
                    </div>
                    <SkillChips skill={[...delta.added].sort(bySkillName)} tone="must" />
                    {delta.added.length === 0 && (
                      <span className="text-[10px] text-ink-faint">无新增</span>
                    )}
                  </div>
                  <div>
                    <div className="flex items-center gap-1 text-xs text-state-declining font-medium mb-1">
                      <ArrowDownRight className="size-3.5" />移除（{delta.removed.length}）
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {[...delta.removed].sort(bySkillName).map((s, i) => (
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
                    <div className="flex flex-wrap items-center gap-1">
                      <SkillChips
                        skill={
                          unchangedExpanded
                            ? [...delta.unchanged].sort(bySkillName)
                            : [...delta.unchanged].sort(bySkillName).slice(0, UNCHANGED_PREVIEW)
                        }
                        tone="nice"
                      />
                      {delta.unchanged.length === 0 && (
                        <span className="text-[10px] text-ink-faint">无变化</span>
                      )}
                      {delta.unchanged.length > UNCHANGED_PREVIEW && (
                        <button
                          type="button"
                          className="text-[10px] text-primary hover:underline"
                          onClick={() => setUnchangedExpanded((v) => !v)}
                        >
                          {unchangedExpanded ? '收起' : `展开全部 ${delta.unchanged.length} 个`}
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              )
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
          <RefreshButton loading={loading} onClick={() => { setLoading(true); setReloadKey((k) => k + 1) }}>
            刷新
          </RefreshButton>
        }
      />

      {/* 顶部指标卡 */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
        <MetricCard data={{ label: '近 30 天候选', value: total, delta: 0, deltaTone: 'stable', hint: '进入发现候选池', bar: true }} />
        <MetricCard data={{ label: '已聚合成图', value: withSkillsCount, delta: 0, deltaTone: 'stable', hint: '有图谱技能清单', bar: true }} />
        <MetricCard data={{ label: '待审核', value: pendingCount, delta: 0, deltaTone: 'declining', hint: 'candidate 态，技能待聚合', bar: true }} />
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
