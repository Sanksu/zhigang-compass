/**
 * 新岗位发现页 — 设计文档 §7 动态演化与新岗位发现（08-27）
 *
 * 数据来源：真实后端 API
 * - GET /api/v1/discovery/recent → 近期新岗位 + 图谱技能（近 30 天候选）
 * - GET /api/v1/discovery/position-skills-delta?position=... → 岗位技能增减（最近两版）
 */
import { useEffect, useState } from 'react'
import { Sparkles, Radar, ArrowUpRight, ArrowDownRight, Minus, AlertCircle } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { formatDateTime } from '@/lib/utils'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { PositionStateBadge } from '@/components/shared/position-state-badge'
import { SkillChip, type SkillChipTone } from '@/components/shared/skill-chips'
import { RefreshButton } from '@/components/shared/refresh-button'
import { apiGet } from '@/lib/api'
import {
  type DiscoveryRecentData,
  type PositionSkillsDeltaData,
  type PositionSkillsDeltaSummaryData,
} from '@/components/discovery/types'
import { MetricCard } from '@/components/shared/metric-card'
import { Reveal } from '@/components/ui/reveal'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

type Tab = 'new' | 'delta'

/** 未变技能默认展示数（超过则折叠，展开按钮显示全量） */
const UNCHANGED_PREVIEW = 12

/** 稳定岗位面板默认展示数 */
const STABLE_PREVIEW = 24

function stateBadge(state: string) {
  return <PositionStateBadge state={state} className="text-[11px]" />
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
      {data.candidates.map((c, i) => {
        const isOpen = expanded.has(c.position_name)
        const mustCount = c.skills?.must?.length ?? 0
        const niceCount = c.skills?.nice?.length ?? 0
        return (
          <Reveal key={c.position_name} delay={i * 60}>
            <Card>
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
                  <span className="text-[11px] text-ink-faint font-mono ml-auto">
                    发现于 {formatDateTime(c.detected_at)}
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
                      <div className="text-[11px] text-ink-faint">必备技能</div>
                      <SkillChips skill={c.skills.must ?? []} tone="must" />
                      <div className="text-[11px] text-ink-faint mt-2">加分技能</div>
                      <SkillChips skill={c.skills.nice ?? []} tone="nice" />
                      <div className="text-[11px] text-ink-faint mt-2">软技能</div>
                      <SkillChips skill={c.skills.soft ?? []} tone="soft" />
                    </>
                  )}
                </div>
              )}
            </CardContent>
            </Card>
          </Reveal>
        )
      })}
    </div>
  )
}

/** 岗位技能增减视图：快照对选择（多版可比）→ 仅列有增减岗位的下拉 → 增减明细 → 稳定岗位面板 */
function SkillsDeltaView() {
  const [summary, setSummary] = useState<PositionSkillsDeltaSummaryData | null>(null)
  const [summaryError, setSummaryError] = useState<string | null>(null)
  // 首载即 loading（mount effect 内不可同步 setState——react-hooks/set-state-in-effect）
  const [summaryLoading, setSummaryLoading] = useState(true)
  const [fromV, setFromV] = useState('')
  const [toV, setToV] = useState('')
  const [selected, setSelected] = useState('')
  const [delta, setDelta] = useState<PositionSkillsDeltaData | null>(null)
  const [deltaError, setDeltaError] = useState<string | null>(null)
  const [deltaLoading, setDeltaLoading] = useState(false)
  // 未变技能折叠：聚合岗位技能可达上百个，全量渲染会把新增/移除挤出首屏
  const [unchangedExpanded, setUnchangedExpanded] = useState(false)
  // 稳定岗位面板折叠（岗位多时同样收敛首屏）
  const [stableExpanded, setStableExpanded] = useState(false)

  // 拉取汇总（缺省最近两期；显式 from/to 支持跨多版对比）。
  // 无同步 setState——loading 置位由初值/mount 前的事件回调负责
  const loadSummary = (fv?: string, tv?: string) => {
    apiGet<PositionSkillsDeltaSummaryData>('/discovery/position-skills-delta/summary', {
      params: fv && tv ? { from_version: fv, to_version: tv } : undefined,
    })
      .then((r) => {
        setSummary(r)
        setSummaryError(null)
        // 首次加载用响应回填选择器（后续由选择器驱动）
        if (!fv || !tv) {
          setFromV(r.from_version ?? '')
          setToV(r.to_version ?? '')
        }
      })
      .catch((e) => {
        setSummary(null)
        setSummaryError(e?.message || '快照对比加载失败')
      })
      .finally(() => setSummaryLoading(false))
  }

  useEffect(() => {
    loadSummary()
  }, [])

  // 切换快照对：复位岗位选择与折叠态后重拉汇总（事件回调内同步置位 loading）
  const handleVersionChange = (nextFrom: string, nextTo: string) => {
    if (!nextFrom || !nextTo || nextFrom === nextTo) return
    setFromV(nextFrom)
    setToV(nextTo)
    setSelected('')
    setDelta(null)
    setDeltaError(null)
    setUnchangedExpanded(false)
    setSummaryLoading(true)
    loadSummary(nextFrom, nextTo)
  }

  // 增减明细：跟随岗位 + 快照对（loading 置位在选择事件回调，effect 内不同步 setState）
  useEffect(() => {
    if (!selected || !fromV || !toV) return
    let cancelled = false
    apiGet<PositionSkillsDeltaData>('/discovery/position-skills-delta', {
      params: { position: selected, from_version: fromV, to_version: toV },
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
  }, [selected, fromV, toV])

  // 后端按 skill_id 排序（对用户无意义），展示前按技能名排序
  const bySkillName = (a: { skill_name: string }, b: { skill_name: string }) =>
    a.skill_name.localeCompare(b.skill_name, 'zh-Hans-CN')

  // 快照日期（版本 id 无阅读意义，放 title 溯源）
  const fmtSnapDate = (iso: string | null | undefined) => formatDateTime(iso)

  const versionOptions = summary?.versions ?? []
  // 下拉仅显示有增减的岗位；稳定岗位归入下方独立面板
  const changedPositions = (summary?.positions ?? []).filter((p) => p.added + p.removed > 0)
  const stablePositions = (summary?.positions ?? []).filter(
    (p) => p.added === 0 && p.removed === 0 && p.unchanged > 0,
  )

  const selectItem = (name: string, value: string, disabled?: boolean) => (
    <SelectItem key={value} value={value} disabled={disabled}>
      {name}
    </SelectItem>
  )

  return (
    <div className="space-y-4">
      <Reveal delay={0}>
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">岗位技能增减（快照对比）</CardTitle>
          <CardDescription>
            任选两个图谱版本快照对比；下拉仅列出该对比范围内有技能增减的岗位
          </CardDescription>
        </CardHeader>
        <CardContent>
          {summaryError ? (
            <p className="text-xs text-state-archived text-center py-6">{summaryError}</p>
          ) : summaryLoading ? (
            <p className="text-xs text-ink-muted text-center py-6">加载快照对比…</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2 mb-4">
                <Select value={fromV || undefined} onValueChange={(v) => handleVersionChange(v, toV)}>
                  <SelectTrigger className="h-8 w-52 text-xs" aria-label="起始快照">
                    <SelectValue placeholder="起始快照" />
                  </SelectTrigger>
                  <SelectContent>
                    {versionOptions.map((v) =>
                      selectItem(`${fmtSnapDate(v.created_at)} · ${v.id}`, v.id, v.id === toV),
                    )}
                  </SelectContent>
                </Select>
                <span className="text-[11px] text-ink-faint">→</span>
                <Select value={toV || undefined} onValueChange={(v) => handleVersionChange(fromV, v)}>
                  <SelectTrigger className="h-8 w-52 text-xs" aria-label="目标快照">
                    <SelectValue placeholder="目标快照" />
                  </SelectTrigger>
                  <SelectContent>
                    {versionOptions.map((v) =>
                      selectItem(`${fmtSnapDate(v.created_at)} · ${v.id}`, v.id, v.id === fromV),
                    )}
                  </SelectContent>
                </Select>
                {changedPositions.length === 0 && summary && (
                  <span className="text-[11px] text-ink-faint self-center">
                    该对比范围内无岗位技能增减
                  </span>
                )}
              </div>

              {changedPositions.length > 0 && (
                <div className="flex flex-wrap gap-2 mb-4">
                  <Select value={selected || 'none'} onValueChange={(v) => {
                    setSelected(v === 'none' ? '' : v)
                    setDeltaLoading(v !== 'none')
                    setUnchangedExpanded(false)
                  }}>
                    <SelectTrigger className="h-8 w-72 text-xs" aria-label="岗位">
                      <SelectValue placeholder="选择岗位（仅列有增减）…" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">选择岗位（仅列有增减）…</SelectItem>
                      {changedPositions.map((p) =>
                        selectItem(
                          `${p.position_name}（+${p.added} −${p.removed}）`,
                          p.position_id,
                        ),
                      )}
                    </SelectContent>
                  </Select>
                </div>
              )}

              {deltaLoading && (
                <p className="text-xs text-ink-muted text-center py-6">加载技能增减…</p>
              )}
              {deltaError && !deltaLoading && (
                <p className="text-xs text-state-archived text-center py-6">{deltaError}</p>
              )}
              {delta && !deltaLoading && (
                delta.added.length + delta.removed.length + delta.unchanged.length === 0 ? (
                  <p className="text-xs text-ink-faint text-center py-6">
                    该岗位两版快照间无技能数据
                  </p>
                ) : (
                  <div className="space-y-3">
                    {/* 全新岗位解读：目标版首次入图，无基线可比 */}
                    {delta.added.length > 0 && delta.removed.length === 0 && delta.unchanged.length === 0 && (
                      <p className="text-[11px] text-ink-faint">
                        该岗位在目标快照首次出现，全部技能计为新增
                      </p>
                    )}
                    <div>
                      <div className="flex items-center gap-1 text-xs text-state-stable font-medium mb-1">
                        <ArrowUpRight className="size-3.5" />新增（{delta.added.length}）
                      </div>
                      <SkillChips skill={[...delta.added].sort(bySkillName)} tone="must" />
                      {delta.added.length === 0 && (
                        <span className="text-[11px] text-ink-faint">无新增</span>
                      )}
                    </div>
                    <div>
                      <div className="flex items-center gap-1 text-xs text-state-declining font-medium mb-1">
                        <ArrowDownRight className="size-3.5" />移除（{delta.removed.length}）
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {[...delta.removed].sort(bySkillName).map((s, i) => (
                          <span key={`${s.skill_id}-${i}`} className="rounded bg-state-archived/10 px-1.5 py-0.5 text-[11px] text-state-archived line-through">
                            {s.skill_name}
                          </span>
                        ))}
                        {delta.removed.length === 0 && (
                          <span className="text-[11px] text-ink-faint">无移除</span>
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
                          <span className="text-[11px] text-ink-faint">无变化</span>
                        )}
                        {delta.unchanged.length > UNCHANGED_PREVIEW && (
                          <button
                            type="button"
                            className="text-[11px] text-primary hover:underline"
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
      </Reveal>

      {/* 稳定面板：两版间技能集合完全一致（有技能且零增减）的岗位 */}
      <Reveal delay={140}>
        <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">稳定岗位（无技能增减）</CardTitle>
          <CardDescription>
            对比范围内技能集合与基准快照完全一致的岗位，反映岗位技能需求的稳定面
          </CardDescription>
        </CardHeader>
        <CardContent>
          {summaryLoading ? (
            <p className="text-xs text-ink-muted text-center py-4">加载稳定岗位…</p>
          ) : stablePositions.length === 0 ? (
            <p className="text-xs text-ink-faint text-center py-4">
              所选快照间无稳定岗位（全部岗位均有技能增减）
            </p>
          ) : (
            <div className="flex flex-wrap items-center gap-1.5">
              {(stableExpanded ? stablePositions : stablePositions.slice(0, STABLE_PREVIEW)).map(
                (p) => (
                  <SkillChip
                    key={p.position_id}
                    tone="nice"
                    title={`新增 0 · 移除 0 · 未变 ${p.unchanged} 项`}
                  >
                    {p.position_name} · {p.unchanged} 项未变
                  </SkillChip>
                ),
              )}
              {stablePositions.length > STABLE_PREVIEW && (
                <button
                  type="button"
                  className="text-[11px] text-primary hover:underline"
                  onClick={() => setStableExpanded((v) => !v)}
                >
                  {stableExpanded ? '收起' : `展开全部 ${stablePositions.length} 个`}
                </button>
              )}
            </div>
          )}
        </CardContent>
      </Card>
      </Reveal>
    </div>
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
        {([
          { label: '近 30 天候选', value: total, hint: '进入发现候选池', tone: 'stable' },
          { label: '已聚合成图', value: withSkillsCount, hint: '有图谱技能清单', tone: 'stable' },
          { label: '待审核', value: pendingCount, hint: 'candidate 态，技能待聚合', tone: 'declining' },
        ] as { label: string; value: number; hint: string; tone: 'stable' | 'declining' }[]).map((s, i) => (
          <Reveal key={s.label} delay={i * 90} className="h-full">
            <MetricCard
              className="h-full"
              data={{ label: s.label, value: s.value, delta: 0, deltaTone: s.tone, hint: s.hint, bar: true }}
            />
          </Reveal>
        ))}
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
        <SkillsDeltaView />
      )}
    </>
  )
}
