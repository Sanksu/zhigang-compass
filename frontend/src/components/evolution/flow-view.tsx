/** 演化视图组件（从 evolution-page.tsx 抽出，第六轮审查拆分：页面 ≤800 行惯例）。 */
import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { GitBranch } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { apiGet, errMsg } from '@/lib/api'
import { isDark } from '@/lib/utils'
import type { components } from '@/types/api'
import type { SkillEvolutionData, SkillEvolutionListData } from './types'
import { SearchableSelect } from './shared'

type SkillFlowData = components['schemas']['SkillFlowData']

/** 技能关联岗位动态变迁桑基图：列=快照期次，节点=该期 Top-N 岗位，
 * 连线=相邻期同名岗位（值=左侧期次频次）——输入技能看关联岗位进出变迁。 */
export function SkillFlowView() {
  const [skills, setSkills] = useState<SkillEvolutionData[] | null>(null)
  const [skillId, setSkillId] = useState('')
  const [flow, setFlow] = useState<SkillFlowData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
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

  useEffect(() => {
    const el = elRef.current
    if (!el || !flow || flow.nodes.length === 0) return
    const dark = isDark()
    const axisColor = dark ? '#334155' : '#e2e8f0'
    const chart = echarts.init(el)
    chartRef.current = chart
    const nameOf = new Map(flow.nodes.map((n) => [n.id, n.name]))
    chart.setOption({
      animation: true,
      tooltip: {
        trigger: 'item',
        backgroundColor: dark ? '#1e293b' : '#fff',
        borderColor: axisColor,
        textStyle: { color: dark ? '#e2e8f0' : '#1e293b', fontSize: 11 },
        formatter: (p: {
          dataType: string
          data: { source?: string; target?: string; name?: string; value?: number }
        }) => {
          if (p.dataType === 'edge') {
            const from = nameOf.get(p.data.source ?? '')
            const to = nameOf.get(p.data.target ?? '')
            return from === to
              ? `${from}<br/>持续需求 · 频次 ${p.data.value}`
              : `${from} → ${to}<br/>频次 ${p.data.value}`
          }
          const node = flow.nodes.find((n) => n.id === p.data.name)
          if (!node) return ''
          return `${node.name}<br/>${flow.periods[node.period_index] ?? '—'} · 频次 ${node.freq}`
        },
      },
      series: [
        {
          type: 'sankey',
          left: 16,
          right: 130,
          top: 16,
          bottom: 16,
          nodeWidth: 12,
          nodeGap: 6,
          emphasis: { focus: 'adjacency' },
          label: {
            fontSize: 10,
            color: dark ? '#cbd5e1' : '#334155',
            formatter: (p: { data: { name?: string } }) =>
              nameOf.get(p.data.name ?? '') ?? p.data.name ?? '',
          },
          lineStyle: { color: 'gradient', curveness: 0.5, opacity: 0.35 },
          data: flow.nodes.map((n) => ({ name: n.id })),
          links: flow.links.map((l) => ({
            source: l.source,
            target: l.target,
            value: l.value,
          })),
        },
      ],
    })
    const observer = new ResizeObserver(() => chartRef.current?.resize())
    observer.observe(el)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [flow])

  return (
    <Card className="mb-4">
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <span className="flex items-center gap-2">
            <GitBranch className="size-4" />
            <span>技能关联岗位变迁桑基图</span>
            <span className="text-[11px] font-normal text-ink-faint">
              输入技能 → 各期 Top-8 关联岗位进出与持续需求厚度
            </span>
          </span>
          <div className="flex items-center gap-2">
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
        {flow && flow.nodes.length > 0 && (
          <>
            <div ref={elRef} className="h-96 w-full" />
            <p className="mt-1 text-[11px] text-ink-faint">
              {flow.skill_name} · 共 {flow.periods.length} 期快照（
              {flow.periods[0] ?? '—'} → {flow.periods[flow.periods.length - 1] ?? '—'}）·
              连线粗细=左侧期次 REQUIRES 频次
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}

