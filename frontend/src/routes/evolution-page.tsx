/**
 * 演化看板页 — 设计文档 §7 动态演化与新岗位发现
 *
 * 数据来源：真实后端 API
 * - GET /api/v1/evolution/versions → 版本列表（顶部指标 + diff 下拉）
 * - GET /api/v1/evolution/versions/{id} → 版本详情弹窗
 * - GET /api/v1/evolution/diff      → 版本快照差异
 * - GET /api/v1/evolution/trends    → 技能频次趋势
 * - GET /api/v1/evolution/signals   → 新兴/衰退信号
 * - GET /api/v1/evolution/position/{id}/evolution → 岗位演化历史
 * - GET /api/v1/evolution/state-machine → 岗位状态机流转（六态分布 + 人工审核记录）
 */
import { useEffect, useMemo, useState } from 'react'
import { Calendar, TrendingUp, TrendingDown } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { apiGet } from '@/lib/api'
import type { components } from '@/types/api'
import type {
  EvolutionVersion,
  EvolutionSignal,
  EvolutionSignalsData,
  MetricItem,
} from '@/components/evolution/types'
import { MetricCard } from '@/components/evolution/shared'
import { SkillFlowView } from '@/components/evolution/flow-view'
import { VersionDiffView } from '@/components/evolution/version-diff'
import { StateMachineView, EvolutionEventsView, DataWarningBanner } from '@/components/evolution/state-views'
import { TechnologyWatchView, SkillTrendView, PositionEvolutionView } from '@/components/evolution/watch-views'

// ===== SignalsView =====

/** 信号模块级共享缓存（60s TTL + 单飞）：同页 SkillDeclineWarningCard 与
 * SignalsView 原各自请求（top_n=8/10 重复拉取），合并为一次共享。
 * 口径与后端 Redis 缓存对齐；失败静默由各消费方自行降级。 */
let signalsCache: { at: number; data: EvolutionSignalsData } | null = null
let signalsPromise: Promise<EvolutionSignalsData | null> | null = null
function loadSignals(): Promise<EvolutionSignalsData | null> {
  const now = Date.now()
  if (signalsCache && now - signalsCache.at < 60_000) {
    return Promise.resolve(signalsCache.data)
  }
  if (signalsPromise) return signalsPromise
  signalsPromise = apiGet<EvolutionSignalsData>('/evolution/signals?top_n=10')
    .then((r) => {
      signalsCache = { at: now, data: r }
      return r
    })
    .catch(() => null)
    .finally(() => {
      signalsPromise = null
    })
  return signalsPromise
}

/** 新兴/衰退技能 Top-N（共享 loadSignals，真实 GET /evolution/signals） */
function SignalsView() {
  const [data, setData] = useState<EvolutionSignalsData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    loadSignals().then((r) => {
      if (r) setData(r)
      else setError('信号加载失败')
    })
  }, [])

  if (error) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-xs text-state-archived">{error}</CardContent>
      </Card>
    )
  }
  if (!data) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-xs text-ink-muted">加载演化信号…</CardContent>
      </Card>
    )
  }

  function renderList(items: EvolutionSignal[], tone: 'emerging' | 'declining', windowCount: number) {
    if (items.length === 0) {
      // 空态即方法论展示（视觉评审 P2）：0 信号不是"没数据"，是阈值未触发
      return (
        <div className="py-6 text-center">
          <p className="text-xs text-ink-muted">当前窗口无统计显著涨落 —— Z-score 阈值未触发</p>
          <p className="mt-1 text-[10px] text-ink-faint">
            {windowCount < 2
              ? `历史快照不足（当前 ${windowCount} 期，需 ≥2 期），冷启动阶段暂不判定`
              : `判定口径：${tone === 'emerging' ? '新兴 z > 2.0' : '衰退 z < -1.5'}（${windowCount} 期滑窗 · 频次占比归一）；涨落越过阈值后在此列出`}
          </p>
        </div>
      )
    }
    const toneColor = tone === 'emerging' ? 'text-state-emerging' : 'text-state-declining'
    return (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8">#</TableHead>
            <TableHead>技能</TableHead>
            <TableHead className="text-right">Z-score</TableHead>
            <TableHead className="text-right">当期频次</TableHead>
            <TableHead className="text-right">占比口径</TableHead>
            <TableHead className="text-right">置信度</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((s, i) => (
            <TableRow key={s.skill_id}>
              <TableCell className="text-xs font-mono text-ink-faint">{i + 1}</TableCell>
              <TableCell className="font-medium text-ink">
                {s.skill_name}
                {s.warning && (
                  <span
                    title="证据量异常期（样本量对比告警命中），信号读数受采集波动影响，谨慎解读"
                    className="ml-1.5 inline-flex items-center rounded-sm border border-state-declining/40 bg-state-declining/10 px-1 text-[10px] font-normal text-state-declining"
                  >
                    ⚠ 证据量异常
                  </span>
                )}
              </TableCell>
              <TableCell className={cn('text-right font-mono tabular-nums', toneColor)}>
                {s.z_score != null ? s.z_score.toFixed(2) : '—'}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums text-ink-secondary">{s.current_freq}</TableCell>
              <TableCell className="text-right font-mono tabular-nums text-ink-faint">
                {s.freq_ratio != null ? `${(s.freq_ratio * 100).toFixed(1)}%` : '—'}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums text-ink-muted">
                {(s.confidence * 100).toFixed(0)}%
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    )
  }

  return (
    <div className="mb-4">
      {(data.warnings?.length ?? 0) > 0 && (
        <Card className="mb-4 border-state-declining/30 bg-state-declining/5">
          <CardContent className="py-3 text-xs text-state-declining">
            ⚠ 采样窗口内 {data.warnings!.length} 个图谱版本命中样本量对比告警
            （证据量萎缩 &lt;50% 或膨胀 &gt;200%，见版本列表）；信号已打「证据量异常」标，
            判定口径不受影响，解读时请注意采集波动。
          </CardContent>
        </Card>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              <TrendingUp className="size-4 text-state-emerging" />
              <span>新兴技能 Top-10</span>
              <span className="text-[10px] font-normal text-ink-faint">z &gt; 2.0</span>
            </CardTitle>
          </CardHeader>
          <CardContent>{renderList(data.emerging, 'emerging', data.window_count)}</CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              <TrendingDown className="size-4 text-state-declining" />
              <span>衰退技能 Top-10</span>
              <span className="text-[10px] font-normal text-ink-faint">z &lt; -1.5</span>
            </CardTitle>
          </CardHeader>
          <CardContent>{renderList(data.declining, 'declining', data.window_count)}</CardContent>
        </Card>
      </div>
    </div>
  )
}

// ===== SkillDeclineWarningCard =====

/** C 端技能衰退预警摘要卡（风险治理引导）：declining Top-N 一眼可见。

 * 数据共享 loadSignals（与 SignalsView 同页一次拉取，不再独立请求）；
 * 衰退技能以橙徽标 + Z-score 悬浮提示呈现；无信号不渲染（不留占位）。 */
function SkillDeclineWarningCard() {
  const [declining, setDeclining] = useState<EvolutionSignal[] | null>(null)

  useEffect(() => {
    let cancelled = false
    loadSignals().then((r) => {
      if (!cancelled) setDeclining(r ? r.declining.slice(0, 8) : [])
    })
    return () => {
      cancelled = true
    }
  }, [])

  if (!declining || declining.length === 0) return null
  return (
    <Card className="mb-4 border-state-declining/30 bg-state-declining/5">
      <CardHeader className="pb-1">
        <CardTitle className="flex items-center gap-2 text-sm text-state-declining">
          <TrendingDown className="size-4" />
          <span>技能衰退预警 · {declining.length} 项</span>
          <span className="text-[10px] font-normal text-ink-faint">
            以下技能需求呈衰退信号（Z &lt; -1.5），求职者请关注学习路径中的替代技能
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-wrap gap-1.5">
        {declining.map((s) => (
          <Badge
            key={s.skill_id}
            variant="outline"
            className="bg-state-declining/10 text-[11px] text-state-declining"
            title={
              s.warning
                ? `${s.skill_name}：Z=${s.z_score?.toFixed(2) ?? '—'}（证据量异常期，谨慎解读）`
                : `${s.skill_name}：Z=${s.z_score?.toFixed(2) ?? '—'} · 频次 ${s.current_freq}`
            }
          >
            {s.skill_name}
          </Badge>
        ))}
      </CardContent>
    </Card>
  )
}

// ===== Page =====

export function EvolutionPage() {
  const [versions, setVersions] = useState<EvolutionVersion[]>([])

  // 加载真实版本列表（顶部指标 + diff 下拉共用），按 version_id 降序保证稳定
  useEffect(() => {
    apiGet<components['schemas']['EvolutionVersionListData']>('/evolution/versions?page=1&size=30')
      .then((res) => setVersions([...res.items].sort((a, b) => b.version_id.localeCompare(a.version_id))))
      .catch(() => {
        /* diff 视图内会提示错误 */
      })
  }, [])

  const metrics = useMemo<MetricItem[]>(() => {
    const latest = versions[0]
    return [
      { key: 'total', label: '图谱版本数', value: versions.length, delta: versions.length, tone: 'stable', hint: 'T+1 05:00 发布 · 保留 90 天' },
      { key: 'version', label: '当前版本号', value: latest?.version_id ?? '—', delta: 0, tone: 'stable', hint: latest?.change_summary || '暂无版本快照' },
      { key: 'nodes', label: '最新版本节点变化', value: latest ? latest.node_added + latest.node_changed : 0, delta: latest?.node_added ?? 0, tone: 'emerging', hint: `新增 ${latest?.node_added ?? 0} · 变化 ${latest?.node_changed ?? 0}` },
      { key: 'signals', label: '新兴/衰退信号', value: '—', delta: 0, tone: 'stable', hint: '下方"新兴/衰退技能 Top-10"实时展示' },
    ]
  }, [versions])

  return (
    <>
      <PageHeader
        title="演化看板"
        description="图谱版本快照追踪技能频次变化，Z-score 检测新兴/衰退信号 · 岗位状态机生命周期管理"
        actions={
          <Badge variant="outline" className="font-mono text-xs">
            <Calendar className="size-3 mr-1" />
            T+1 05:00 发布
          </Badge>
        }
      />

      {/* 样本量波动告警 + 顶部指标卡（真实版本派生） */}
      {versions[0]?.data_warning && <DataWarningBanner warning={versions[0].data_warning} />}

      {/* C 端技能衰退预警摘要（真实 /evolution/signals declining） */}
      <SkillDeclineWarningCard />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
        {metrics.map((m) => (
          <MetricCard key={m.key} metric={m} />
        ))}
      </div>

      {/* 岗位演化历史（真实 /evolution/position/{id}/evolution） */}
      <div className="mb-4">
        <PositionEvolutionView />
      </div>

      {/* 技能频次趋势（真实 /evolution/trends） */}
      <div className="mb-4">
        <SkillTrendView />
      </div>

      {/* 技能关联岗位动态变迁桑基图（真实 /evolution/skill/{id}/flow） */}
      <SkillFlowView />

      {/* 新兴 / 衰退技能 Top-10（真实 /evolution/signals） */}
      <SignalsView />

      {/* 技术热点观察池（真实 /evolution/watch，MLI 产业化拐点） */}
      <TechnologyWatchView />

      {/* 版本快照对比（真实） */}
      <div className="mb-4">
        <VersionDiffView />
      </div>

      {/* 岗位状态机流转（真实 /evolution/state-machine） */}
      <StateMachineView />

      {/* 谱系事件流（真实 /evolution/events，新增/合并/终结） */}
      <div className="mt-4">
        <EvolutionEventsView />
      </div>
    </>
  )
}

