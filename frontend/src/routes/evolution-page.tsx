/**
 * 演化看板页 — 设计文档 §7 动态演化与新岗位发现
 *
 * 数据来源：真实后端 API
 * - GET /api/v1/evolution/versions → 版本列表（顶部指标 + diff 下拉）
 * - GET /api/v1/evolution/diff      → 版本快照差异
 * - GET /api/v1/evolution/trends    → 技能频次趋势（待接入）
 *
 * 后端未产出的部分（新兴/衰退信号、技能趋势、状态机流转）显示空态，等待 M4。
 */
import { useEffect, useMemo, useState } from 'react'
import { Calendar, GitBranch } from 'lucide-react'
import { PageHeader } from '@/components/layout/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import { apiGet } from '@/lib/api'

// ===== Types =====

type TrendTone = 'emerging' | 'declining' | 'stable'

interface VersionDiffItem {
  name: string
  type: 'position' | 'skill' | 'evidence'
  change: 'added' | 'removed' | 'changed'
  detail: string
}

interface MetricItem {
  key: string
  label: string
  value: string | number
  delta: number
  tone: TrendTone
  hint: string
}

/** 后端 /evolution/versions 返回项 */
interface EvolutionVersion {
  version_id: string
  created_at: string | null
  change_summary: string
  node_added: number
  node_removed: number
  node_changed: number
}

/** 后端 /evolution/diff 返回项 */
interface EvolutionDiff {
  nodes_added: string[]
  nodes_removed: string[]
  nodes_changed: string[]
  edges_added: string[]
  edges_removed: string[]
}

// ===== MetricCard =====

function MetricCard({ metric }: { metric: MetricItem }) {
  const toneColor =
    metric.tone === 'emerging'
      ? 'text-state-emerging'
      : metric.tone === 'declining'
        ? 'text-state-declining'
        : 'text-state-stable'
  const toneBg =
    metric.tone === 'emerging'
      ? 'bg-state-emerging/10'
      : metric.tone === 'declining'
        ? 'bg-state-declining/10'
        : 'bg-state-stable/10'

  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-ink-muted">{metric.label}</span>
          <span className={cn('inline-flex items-center gap-0.5 text-xs font-mono', toneColor)}>
            {metric.delta > 0 ? '+' : ''}{metric.delta}
          </span>
        </div>
        <div className="text-2xl font-semibold tracking-tight tabular-nums">
          {typeof metric.value === 'number' ? (
            metric.value.toLocaleString()
          ) : (
            <span className="font-mono">{metric.value}</span>
          )}
        </div>
        <div className="text-[10px] text-ink-faint mt-1 truncate">{metric.hint}</div>
        <div className={cn('mt-2 h-0.5 rounded-full', toneBg)} />
      </CardContent>
    </Card>
  )
}

// ===== VersionDiffView =====

/** 从节点 ID 前缀推断类型（id_generator 约定 pos_/sk_/ev_） */
function typeOf(id: string): VersionDiffItem['type'] {
  if (id.startsWith('pos_')) return 'position'
  if (id.startsWith('ev_')) return 'evidence'
  return 'skill'
}

function diffToItems(d: EvolutionDiff): {
  added: VersionDiffItem[]
  removed: VersionDiffItem[]
  changed: VersionDiffItem[]
} {
  return {
    added: d.nodes_added.map((id) => ({ name: id, type: typeOf(id), change: 'added', detail: '节点新增' })),
    removed: d.nodes_removed.map((id) => ({ name: id, type: typeOf(id), change: 'removed', detail: '节点删除' })),
    changed: d.nodes_changed.map((id) => ({ name: id, type: typeOf(id), change: 'changed', detail: '节点属性变化' })),
  }
}

function VersionDiffView() {
  const [versions, setVersions] = useState<EvolutionVersion[]>([])
  const [v1, setV1] = useState<string>('')
  const [v2, setV2] = useState<string>('')
  // diff 绑定请求时的版本对，渲染层据此判断当前结果是否仍有效（避免 effect 内同步 setState）
  const [diff, setDiff] = useState<{
    v1: string
    v2: string
    data: { added: VersionDiffItem[]; removed: VersionDiffItem[]; changed: VersionDiffItem[] }
  } | null>(null)
  const [error, setError] = useState<string | null>(null)

  // 加载真实版本列表，默认对比最近两个版本
  useEffect(() => {
    apiGet<{ items: EvolutionVersion[]; total: number }>('/evolution/versions?page=1&size=30')
      .then((res) => {
        // 快照可能在同一事务写入导致 created_at 相同，按 version_id（graph_vYYYYMMDD）降序保证稳定
        const items = [...res.items].sort((a, b) => b.version_id.localeCompare(a.version_id))
        setVersions(items)
        if (items.length >= 2) {
          setV1(items[1].version_id)
          setV2(items[0].version_id)
        } else if (items.length === 1) {
          setV1(items[0].version_id)
        }
      })
      .catch(() => setError('版本列表加载失败'))
  }, [])

  // 版本对变化 → 拉取真实 diff（setState 均在异步回调内）
  useEffect(() => {
    if (!v1 || !v2 || v1 === v2) return
    let cancelled = false
    apiGet<EvolutionDiff>(
      `/evolution/diff?from=${encodeURIComponent(v1)}&to=${encodeURIComponent(v2)}`,
    )
      .then((d) => {
        if (!cancelled) setDiff({ v1, v2, data: diffToItems(d) })
      })
      .catch(() => {
        if (!cancelled) setError('版本对比加载失败')
      })
    return () => {
      cancelled = true
    }
  }, [v1, v2])

  // 渲染派生：仅展示与当前版本对匹配的 diff；加载态 = 有版本对但结果未就绪
  const visibleDiff = diff && diff.v1 === v1 && diff.v2 === v2 ? diff.data : null
  const loading = Boolean(v1 && v2 && v1 !== v2 && !visibleDiff && !error)

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <span className="flex items-center gap-2">
            <GitBranch className="size-4" />
            <span>版本快照对比</span>
          </span>
          <div className="flex items-center gap-1.5">
            <Select value={v1} onValueChange={setV1} disabled={versions.length === 0}>
              <SelectTrigger className="h-8 w-[150px] font-mono text-xs">
                <SelectValue placeholder="选择版本" />
              </SelectTrigger>
              <SelectContent>
                {versions.map((v) => (
                  <SelectItem key={v.version_id} value={v.version_id} className="font-mono text-xs">
                    {v.version_id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-xs text-ink-faint">vs</span>
            <Select value={v2} onValueChange={setV2} disabled={versions.length === 0}>
              <SelectTrigger className="h-8 w-[150px] font-mono text-xs">
                <SelectValue placeholder="选择版本" />
              </SelectTrigger>
              <SelectContent>
                {versions.map((v) => (
                  <SelectItem key={v.version_id} value={v.version_id} className="font-mono text-xs">
                    {v.version_id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {error && <div className="py-10 text-center text-xs text-state-archived">{error}</div>}
        {!error && versions.length === 0 && (
          <div className="py-10 text-center text-xs text-ink-muted">暂无图谱版本快照</div>
        )}
        {!error && versions.length > 0 && !v1 && !v2 && (
          <div className="py-10 text-center text-xs text-ink-muted">仅存在单个版本，无法对比</div>
        )}
        {loading && (
          <div className="py-10 text-center text-xs text-ink-muted">加载版本差异…</div>
        )}
        {!loading && visibleDiff && (
          <>
            <div className="grid grid-cols-3 gap-3 mb-4">
              <StatTile label="新增节点" count={visibleDiff.added.length} tone="emerging" />
              <StatTile label="删除节点" count={visibleDiff.removed.length} tone="declining" />
              <StatTile label="变化节点" count={visibleDiff.changed.length} tone="stable" />
            </div>
            <Tabs defaultValue="added">
              <TabsList>
                <TabsTrigger value="added" className="text-xs">新增 ({visibleDiff.added.length})</TabsTrigger>
                <TabsTrigger value="removed" className="text-xs">删除 ({visibleDiff.removed.length})</TabsTrigger>
                <TabsTrigger value="changed" className="text-xs">变化 ({visibleDiff.changed.length})</TabsTrigger>
              </TabsList>
              <TabsContent value="added">
                <DiffTable items={visibleDiff.added} />
              </TabsContent>
              <TabsContent value="removed">
                <DiffTable items={visibleDiff.removed} />
              </TabsContent>
              <TabsContent value="changed">
                <DiffTable items={visibleDiff.changed} />
              </TabsContent>
            </Tabs>
          </>
        )}
        {!loading && !error && versions.length > 0 && v1 && v2 && v1 === v2 && (
          <div className="py-10 text-center text-xs text-ink-muted">请选择两个不同版本进行对比</div>
        )}
      </CardContent>
    </Card>
  )
}

function StatTile({ label, count, tone }: { label: string; count: number; tone: TrendTone }) {
  const color =
    tone === 'emerging'
      ? 'text-state-emerging'
      : tone === 'declining'
        ? 'text-state-declining'
        : 'text-state-stable'
  return (
    <div className="rounded-md border border-border p-3 bg-subtle/40">
      <div className={cn('text-2xl font-semibold tabular-nums', color)}>{count}</div>
      <div className="text-xs text-ink-muted mt-0.5">{label}</div>
    </div>
  )
}

function DiffTable({ items }: { items: VersionDiffItem[] }) {
  if (items.length === 0) {
    return <div className="py-6 text-center text-xs text-ink-faint">无数据</div>
  }
  const typeLabel: Record<VersionDiffItem['type'], string> = {
    position: '岗位',
    skill: '技能',
    evidence: '证据',
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[180px]">节点名</TableHead>
          <TableHead className="w-[60px]">类型</TableHead>
          <TableHead>变化说明</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item, i) => (
          <TableRow key={item.name + item.change + i}>
            <TableCell className="font-medium text-ink">{item.name}</TableCell>
            <TableCell>
              <Badge variant="outline" className="text-[10px] font-mono">
                {typeLabel[item.type]}
              </Badge>
            </TableCell>
            <TableCell className="text-xs text-ink-muted">{item.detail}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** 后端未产出模块的统一空态卡片 */
function PendingCard({ title, hint }: { title: string; hint: string }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-xs text-ink-faint py-10 text-center border border-dashed border-border rounded-md">
          {hint}
        </p>
      </CardContent>
    </Card>
  )
}

// ===== Page =====

export function EvolutionPage() {
  const [versions, setVersions] = useState<EvolutionVersion[]>([])

  // 加载真实版本列表（顶部指标 + diff 下拉共用），按 version_id 降序保证稳定
  useEffect(() => {
    apiGet<{ items: EvolutionVersion[]; total: number }>('/evolution/versions?page=1&size=30')
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
      { key: 'signals', label: '新兴/衰退信号', value: '—', delta: 0, tone: 'stable', hint: '信号检测后端待交付（M4）' },
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

      {/* 顶部指标卡（真实版本派生） */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
        {metrics.map((m) => (
          <MetricCard key={m.key} metric={m} />
        ))}
      </div>

      {/* 技能频次趋势（后端待交付） */}
      <div className="mb-4">
        <PendingCard
          title="技能频次趋势 · 最近 90 天"
          hint="技能频次趋势由演化检测管线产出，等待后端交付（M4）"
        />
      </div>

      {/* 新兴 / 衰退 Top-10（后端待交付） */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <PendingCard title="新兴技能 Top-10" hint="新兴信号（z > 2.0）等待后端交付（M4）" />
        <PendingCard title="衰退技能 Top-10" hint="衰退信号（z < -1.5）等待后端交付（M4）" />
      </div>

      {/* 版本快照对比（真实） */}
      <div className="mb-4">
        <VersionDiffView />
      </div>

      {/* 岗位状态机流转（后端待交付） */}
      <PendingCard title="岗位状态机流转" hint="六状态机流转记录等待后端交付（M4）" />
    </>
  )
}
