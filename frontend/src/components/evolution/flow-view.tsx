/** 演化视图组件（从 evolution-page.tsx 抽出，第六轮审查拆分：页面 ≤800 行惯例）。 */
import { useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { GitBranch } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { apiGet, errMsg } from '@/lib/api'
import { isDark } from '@/lib/utils'
import type { components } from '@/types/api'
import type { SkillEvolutionData, SkillEvolutionListData } from './types'
import { SearchableSelect } from './shared'

type SkillFlowData = components['schemas']['SkillFlowData']

/** 窗口选项：value=期数，0=全部（后端已剔除空期）。 */
const WINDOW_OPTIONS = [
  { label: '近10期', value: 10 },
  { label: '近15期', value: 15 },
  { label: '全部', value: 0 },
]

/** 节点状态色：蓝=持续在榜，绿=新进榜，橙=末次在榜（下期出 Top-N）。 */
const STATE_COLORS = { stable: '#3b82f6', enter: '#10b981', leave: '#f59e0b' } as const
const STATE_TEXT = { stable: '持续在榜', enter: '新进榜', leave: '末次在榜' } as const
type FlowState = keyof typeof STATE_COLORS

type ViewNode = {
  id: string
  name: string
  pi: number // 窗口内重排后的期次序号
  freq: number // 岗位当期要求技能总数（REQUIRES 出度）
  state: FlowState
}

/** 技能关联岗位动态变迁桑基图：列=非空快照期次，节点=当期出度 Top-N 岗位，
 * 连线=相邻期同名岗位（厚度=岗位要求技能数）——输入技能看关联岗位进出变迁。
 * 中段列标签默认隐藏（悬停显示），期次日期标注在图底，避免 10+ 列互相遮挡。 */
export function SkillFlowView() {
  const [skills, setSkills] = useState<SkillEvolutionData[] | null>(null)
  const [skillId, setSkillId] = useState('')
  const [flow, setFlow] = useState<SkillFlowData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [win, setWin] = useState(10)
  const elRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  // 竞态序号守卫（graph-page doSearch 范式）：快速切换技能时慢响应不得
  // 覆盖新结果——桑基图曾显示前一技能的岗位变迁（第六轮审查前端 P1）
  const flowSeqRef = useRef(0)

  function fetchFlow(id: string) {
    if (!id) return
    const seq = ++flowSeqRef.current
    setLoading(true)
    apiGet<SkillFlowData>(`/evolution/skill/${encodeURIComponent(id)}/flow?top=8`)
      .then((r) => {
        if (seq !== flowSeqRef.current) return
        setFlow(r)
        setError(null)
      })
      .catch((e) => {
        if (seq !== flowSeqRef.current) return
        setError(errMsg(e, '岗位变迁加载失败'))
      })
      .finally(() => {
        if (seq === flowSeqRef.current) setLoading(false)
      })
  }

  useEffect(() => {
    let cancelled = false
    apiGet<SkillEvolutionListData>('/evolution/skills?page=1&size=50')
      .then((r) => {
        if (cancelled) return
        setSkills(r.skills)
        if (r.skills.length > 0) {
          setSkillId(r.skills[0].skill_id)
          fetchFlow(r.skills[0].skill_id)
        }
      })
      .catch((e) => {
        if (!cancelled) setError(errMsg(e, '技能列表加载失败'))
      })
    return () => {
      cancelled = true
    }
  }, [])

  /** 窗口切片 + 状态推导 + 同列排序（出榜少者靠上 → 持续岗位近似水平连线）。 */
  const view = useMemo(() => {
    if (!flow || flow.nodes.length === 0) return null
    const start = win > 0 ? Math.max(0, flow.periods.length - win) : 0
    const periods = flow.periods.slice(start)
    const totals = (flow.totals ?? []).slice(start)
    const byId = new Map<string, components['schemas']['SkillFlowNode']>(
      flow.nodes.map((n) => [n.id, n]),
    )
    const hasIn = new Set<string>()
    const hasOut = new Set<string>()
    for (const l of flow.links) {
      if (byId.get(l.source) && byId.get(l.target)) {
        hasOut.add(l.source)
        hasIn.add(l.target)
      }
    }
    const remap = new Map<string, ViewNode>()
    const kept = flow.nodes.filter((n) => n.period_index >= start)
    // 同列排序键=窗口内在榜期数（降序）+岗位名，稳定持久岗位的纵向槽位
    const presence = new Map<string, number>()
    for (const n of kept) presence.set(n.name, (presence.get(n.name) ?? 0) + 1)
    const last = periods.length - 1
    const nodes: ViewNode[] = kept.map((n) => {
      const pi = n.period_index - start
      const incoming = pi > 0 && hasIn.has(n.id)
      const outgoing = pi < last && hasOut.has(n.id)
      // 窗口左缘视为此前已在榜（全序列首期=基线），故不标「新进榜」；
      // 中间期无入线=新进榜，无出线=末次在榜（下期跌出 Top-N）
      const state: FlowState =
        pi === 0 ? 'stable' : !incoming ? 'enter' : pi < last && !outgoing ? 'leave' : 'stable'
      const v: ViewNode = { id: n.id, name: n.name, pi, freq: n.freq, state }
      remap.set(n.id, v)
      return v
    })
    const links = flow.links
      .filter((l) => remap.has(l.source) && remap.has(l.target))
      .map((l) => ({ ...l }))
    const counts: Record<FlowState, number> = { stable: 0, enter: 0, leave: 0 }
    for (const n of nodes) counts[n.state]++
    return { periods, totals, nodes, links, counts }
  }, [flow, win])

  useEffect(() => {
    const el = elRef.current
    if (!el || !view || view.nodes.length === 0) return
    const root: HTMLDivElement = el
    const v = view
    const dark = isDark()
    const axisColor = dark ? '#334155' : '#e2e8f0'
    const textColor = dark ? '#cbd5e1' : '#334155'
    const nameOf = new Map(view.nodes.map((n) => [n.id, n.name]))
    const infoOf = new Map(view.nodes.map((n) => [n.id, n]))
    const chart = echarts.init(el)
    chartRef.current = chart
    const last = view.periods.length - 1
    chart.setOption({
      animation: true,
      tooltip: {
        trigger: 'item',
        backgroundColor: dark ? '#1e293b' : '#fff',
        borderColor: axisColor,
        textStyle: { color: dark ? '#e2e8f0' : '#1e293b', fontSize: 11 },
        formatter: (p: {
          dataType: string
          data: { source?: string; name?: string; value?: number }
        }) => {
          if (p.dataType === 'edge') {
            const n = infoOf.get(p.data.source ?? '')
            return n
              ? `${n.name} · 持续在榜<br/>当期要求技能 ${p.data.value} 项`
              : ''
          }
          const n = infoOf.get(p.data.name ?? '')
          if (!n) return ''
          const date = view.periods[n.pi] ?? '—'
          const total = view.totals[n.pi]
          return [
            `${n.name} · 第 ${n.pi + 1} 期（${date}）`,
            `当期要求技能 ${n.freq} 项${total != null ? ` · 共 ${total} 岗位关联` : ''}`,
            `<span style="color:${STATE_COLORS[n.state]}">●</span> ${STATE_TEXT[n.state]}`,
          ].join('<br/>')
        },
      },
      series: [
        {
          type: 'sankey',
          left: 130,
          right: 130,
          top: 10,
          bottom: 30,
          nodeWidth: 12,
          nodeGap: 6,
          layoutIterations: 0, // 不做交叉优化迭代：按数据序排布，持续岗位近似水平
          emphasis: { focus: 'adjacency' },
          label: {
            fontSize: 11,
            color: textColor,
            formatter: (p: { data: { name?: string } }) =>
              nameOf.get(p.data.name ?? '') ?? p.data.name ?? '',
          },
          lineStyle: { color: 'gradient', curveness: 0.5, opacity: 0.3 },
          data: view.nodes.map((n) => ({
            name: n.id,
            itemStyle: { color: STATE_COLORS[n.state] },
            // 期次轴两端列常显岗位名，中段隐藏（悬停显示），治 10+ 列标签互相遮挡
            label: { show: n.pi === 0 || n.pi === last, position: n.pi === 0 ? 'left' : 'right' },
            emphasis: { label: { show: true, position: n.pi === 0 ? 'left' : 'right' } },
          })),
          links: view.links.map((l) => ({
            source: l.source,
            target: l.target,
            value: l.value,
          })),
        },
      ],
    })

    /** 图底期次日期条：读 sankey 布局后逐列标注（读不到布局退化为等分推算）。 */
    function drawStrip() {
      const n = v.periods.length
      if (n === 0) return
      let xs: number[] | null = null
      try {
        /* eslint-disable @typescript-eslint/no-explicit-any */
        const data = (chart as any).getModel().getSeriesByIndex(0).getData() as any
        const colX = new Array<number>(n).fill(NaN)
        for (let i = 0; i < data.count(); i++) {
          const layout = data.getItemLayout(i)
          if (!layout) continue
          const node = infoOf.get(data.getName(i))
          if (node && Number.isNaN(colX[node.pi])) colX[node.pi] = layout.x + layout.width / 2
        }
        /* eslint-enable @typescript-eslint/no-explicit-any */
        if (colX.every((x) => !Number.isNaN(x))) xs = colX
      } catch {
        /* 布局内部 API 变动时走等分推算 */
      }
      if (!xs) {
        const w = root.clientWidth
        xs = Array.from({ length: n }, (_, i) =>
          130 + (i * (w - 260 - 12)) / Math.max(1, n - 1) + 6,
        )
      }
      chart.setOption({
        graphic: xs.map((x, i) => ({
          id: `flow-strip-${i}`,
          type: 'text',
          x,
          y: root.clientHeight - 8,
          style: {
            text: (v.periods[i] ?? '').slice(5),
            textAlign: 'center',
            textVerticalAlign: 'bottom',
            fill: dark ? '#64748b' : '#94a3b8',
            fontSize: 10,
          },
        })),
      })
    }
    drawStrip()

    const observer = new ResizeObserver(() => {
      chartRef.current?.resize()
      drawStrip()
    })
    observer.observe(el)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [view])

  return (
    <Card className="mb-4">
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <span className="flex items-center gap-2">
            <GitBranch className="size-4" />
            <span>技能关联岗位变迁桑基图</span>
            <span className="text-[11px] font-normal text-ink-faint">
              输入技能 → 各期出度 Top-8 关联岗位进出与需求广度
            </span>
          </span>
          <div className="flex items-center gap-2">
            {flow && flow.periods.length > 10 && (
              <div className="flex items-center gap-1">
                {WINDOW_OPTIONS.map((o) => (
                  <Button
                    key={o.value}
                    size="sm"
                    variant="outline"
                    className={
                      'h-7 px-2 text-[11px] ' +
                      (win === o.value ? 'border-primary/30 bg-primary/10 text-primary' : '')
                    }
                    onClick={() => setWin(o.value)}
                  >
                    {o.label}
                  </Button>
                ))}
              </div>
            )}
            {skills && skills.length > 0 && (
              <SearchableSelect
                value={skillId}
                placeholder="选择技能"
                options={skills.map((s) => ({ value: s.skill_id, label: s.skill_name }))}
                pageSize={10}
                onSelect={(v) => {
                  setSkillId(v)
                  fetchFlow(v)
                }}
              />
            )}
            {loading && <span className="text-[11px] text-ink-faint">加载中…</span>}
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {error && <p className="py-6 text-center text-xs text-state-archived">{error}</p>}
        {!error && flow === null && !loading && (
          <p className="py-6 text-center text-xs text-ink-faint">暂无岗位变迁数据（版本数据不足）</p>
        )}
        {!error && flow !== null && flow.nodes.length === 0 && (
          <p className="py-6 text-center text-xs text-ink-faint">该技能在各版本快照中无关联岗位</p>
        )}
        {view && view.nodes.length > 0 && (
          <>
            <div ref={elRef} className="h-[26rem] w-full" />
            <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-faint">
              <span>
                {flow?.skill_name} · 展示 {view.periods.length} 期（{view.periods[0] ?? '—'} →{' '}
                {view.periods[view.periods.length - 1] ?? '—'}）
              </span>
              {(['stable', 'enter', 'leave'] as const).map((s) => (
                <span key={s} className="flex items-center gap-1">
                  <i className="size-2 rounded-sm" style={{ background: STATE_COLORS[s] }} />
                  {STATE_TEXT[s]} {view.counts[s]}
                </span>
              ))}
            </p>
            <p className="mt-0.5 text-[11px] text-ink-faint">
              每期展示出度 Top-8 岗位（连线厚度=岗位当期要求技能数）· 中段岗位名悬停查看 ·
              底部为快照日期
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}
